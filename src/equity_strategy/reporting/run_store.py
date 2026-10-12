import datetime
import json
import logging
import os
import pathlib
import traceback
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class RunStore:
    """
    Manages dedicated per-run artifact storage, lifecycle status,
    and metadata manifests for backtest executions.

    Enforces:
    - Dedicated directory: `<base_dir>/<unique_id>/`.
    - Strict non-overwrite guarantee: Fails immediately if run directory already exists.
    - Lifecycle tracking: `running` -> `completed` / `failed`.
    - Detailed `metadata.json` manifest with configuration, symbols, timings, and artifact inventory.
    """

    def __init__(
        self,
        run_id: str,
        base_dir: Union[str, pathlib.Path] = "strategy_results",
    ) -> None:
        self.run_id = str(run_id).strip()
        if not self.run_id:
            raise ValueError("run_id cannot be empty")

        self.base_dir = pathlib.Path(base_dir)
        self.run_dir = self.base_dir / self.run_id
        self.metadata_path = self.run_dir / "metadata.json"
        self._start_time: Optional[datetime.datetime] = None

    def create_run_dir(self, overwrite: bool = False) -> pathlib.Path:
        """
        Creates the run directory.
        Fails if directory already exists unless overwrite is explicitly True.
        """
        if self.run_dir.exists():
            if not overwrite:
                raise FileExistsError(
                    f"Run directory already exists: '{self.run_dir}'. "
                    f"Overwriting is not permitted. Please use a unique run_id."
                )
            logger.warning("Overwriting existing run directory: %s", self.run_dir)

        self.run_dir.mkdir(parents=True, exist_ok=True)
        return self.run_dir

    def init_metadata(
        self,
        symbols: List[str],
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Initializes metadata manifest with status 'running' and records start timestamp.
        """
        self._start_time = datetime.datetime.now(datetime.timezone.utc)
        clean_config = self._sanitize_for_json(config or {})

        meta = {
            "run_id": self.run_id,
            "status": "running",
            "start_time": self._start_time.isoformat(),
            "end_time": None,
            "duration_seconds": None,
            "symbols_count": len(symbols),
            "symbols": sorted(list(symbols)),
            "config": clean_config,
            "artifacts": {},
            "error": None,
            "traceback": None,
        }

        self._save_metadata(meta)
        return meta

    def complete_metadata(
        self,
        extra_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Marks run as completed, records duration and scans all generated artifacts.
        """
        meta = self.load_metadata() or {
            "run_id": self.run_id,
            "status": "running",
            "start_time": (self._start_time or datetime.datetime.now(datetime.timezone.utc)).isoformat(),
        }

        end_time = datetime.datetime.now(datetime.timezone.utc)
        meta["status"] = "completed"
        meta["end_time"] = end_time.isoformat()

        if self._start_time is not None:
            meta["duration_seconds"] = round((end_time - self._start_time).total_seconds(), 2)
        elif meta.get("start_time"):
            st = datetime.datetime.fromisoformat(meta["start_time"])
            meta["duration_seconds"] = round((end_time - st).total_seconds(), 2)

        meta["artifacts"] = self._scan_artifacts()

        if extra_info:
            meta["extra_info"] = self._sanitize_for_json(extra_info)

        self._save_metadata(meta)
        return meta

    def fail_metadata(
        self,
        error: str,
        tb: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Marks run as failed, records error message, traceback, and partial artifacts.
        """
        meta = self.load_metadata() or {
            "run_id": self.run_id,
            "status": "running",
            "start_time": (self._start_time or datetime.datetime.now(datetime.timezone.utc)).isoformat(),
        }

        end_time = datetime.datetime.now(datetime.timezone.utc)
        meta["status"] = "failed"
        meta["end_time"] = end_time.isoformat()
        meta["error"] = str(error)
        meta["traceback"] = tb or traceback.format_exc()

        if self._start_time is not None:
            meta["duration_seconds"] = round((end_time - self._start_time).total_seconds(), 2)
        elif meta.get("start_time"):
            st = datetime.datetime.fromisoformat(meta["start_time"])
            meta["duration_seconds"] = round((end_time - st).total_seconds(), 2)

        meta["artifacts"] = self._scan_artifacts()
        self._save_metadata(meta)
        return meta

    def load_metadata(self) -> Optional[Dict[str, Any]]:
        """Loads metadata manifest from disk if it exists."""
        if not self.metadata_path.exists():
            return None
        try:
            with open(self.metadata_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("Could not read metadata at %s: %s", self.metadata_path, exc)
            return None

    def _save_metadata(self, meta: Dict[str, Any]) -> None:
        """Atomically saves metadata dictionary to JSON."""
        with open(self.metadata_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, default=str)

    def _scan_artifacts(self) -> Dict[str, Dict[str, Any]]:
        """Scans the run directory for generated files."""
        artifacts = {}
        if not self.run_dir.exists():
            return artifacts

        for p in self.run_dir.iterdir():
            if p.is_file():
                artifacts[p.name] = {
                    "size_bytes": p.stat().st_size,
                    "modified_time": datetime.datetime.fromtimestamp(
                        p.stat().st_mtime, tz=datetime.timezone.utc
                    ).isoformat(),
                }
        return artifacts

    @classmethod
    def list_runs(
        cls,
        base_dir: Union[str, pathlib.Path] = "strategy_results",
    ) -> List[Dict[str, Any]]:
        """
        Discovers all available runs in base_dir, returning their metadata summary.
        Sorted by start_time descending.
        """
        base = pathlib.Path(base_dir)
        if not base.exists():
            return []

        runs = []
        for d in base.iterdir():
            if not d.is_dir():
                continue

            # Ignore reserved directories like 'logs'
            if d.name in ("logs", "__pycache__", ".ipynb_checkpoints"):
                continue

            meta_file = d / "metadata.json"
            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                        runs.append(meta)
                        continue
                except Exception:
                    pass

            # Fallback metadata for directories without valid metadata.json
            stat = d.stat()
            created_dt = datetime.datetime.fromtimestamp(stat.st_ctime, tz=datetime.timezone.utc)
            runs.append({
                "run_id": d.name,
                "status": "unknown",
                "start_time": created_dt.isoformat(),
                "end_time": None,
                "duration_seconds": None,
                "symbols_count": 0,
                "symbols": [],
                "config": {},
                "artifacts": {},
                "error": None,
            })

        # Sort descending by start_time
        runs.sort(key=lambda r: r.get("start_time") or "", reverse=True)
        return runs

    @staticmethod
    def _sanitize_for_json(obj: Any) -> Any:
        """Recursively sanitizes configuration objects for clean JSON serialization."""
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        if isinstance(obj, (list, tuple, set)):
            return [RunStore._sanitize_for_json(v) for v in obj]
        if isinstance(obj, dict):
            return {str(k): RunStore._sanitize_for_json(v) for k, v in obj.items()}
        if isinstance(obj, pathlib.Path):
            return str(obj)
        if hasattr(obj, "to_dict") and callable(getattr(obj, "to_dict")):
            try:
                return RunStore._sanitize_for_json(obj.to_dict())
            except Exception:
                pass
        return str(obj)
