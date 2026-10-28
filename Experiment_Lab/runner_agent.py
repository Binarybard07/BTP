#!/usr/bin/env python3
"""
Experiment Lab - Runner Agent
=============================
Executes strategy backtest scripts asynchronously through Backtest_Module.
Replicates RM_TM_TF_Combo runner_agent architecture:
- ThreadPoolExecutor concurrency
- Process isolation
- Real-time live log teeing to Backtest_Logs/<run_id>.log
- Preserves ALL Backtest_Module output components in Backtest_Results/
- Automatic script archiving to outbox/processed/ or outbox/Errornous_scripts/
- Resume capability: skips runs that already produced valid summary.json
- Direct execution without flags: `python runner_agent.py`
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Optional
import yaml

# ─────────────────────────────────────────────
# PATHS CONFIGURATION
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CONFIG_FILE = BASE_DIR / "config.yaml"
RUNNER_LOG_FILE = BASE_DIR / "runner_agent.log"

SCRIPTS_INBOX = BASE_DIR / "outbox" / "non_processed"
SCRIPTS_PROCESSED = BASE_DIR / "outbox" / "processed"
SCRIPTS_ERRONOUS = BASE_DIR / "outbox" / "Errornous_scripts"

BACKTEST_DIR = BASE_DIR / "Backtest_Results"
BACKTEST_RESULTS_OUTBOX = BACKTEST_DIR / "outbox"
BACKTEST_RESULTS_INBOX = BACKTEST_DIR / "non_processed"
BACKTEST_RESULTS_PROCESSED = BACKTEST_DIR / "processed"

BACKTEST_LOGS = BASE_DIR / "Backtest_Logs"

for d in (
    SCRIPTS_INBOX,
    SCRIPTS_PROCESSED,
    SCRIPTS_ERRONOUS,
    BACKTEST_DIR,
    BACKTEST_RESULTS_OUTBOX,
    BACKTEST_RESULTS_INBOX,
    BACKTEST_RESULTS_PROCESSED,
    BACKTEST_LOGS,
):
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────
logger = logging.getLogger("runner_agent")
logger.setLevel(logging.INFO)

if not logger.handlers:
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    fh = logging.FileHandler(RUNNER_LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)

    fmt = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    ch.setFormatter(fmt)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)


def load_execution_settings() -> dict:
    """Load concurrency and timeout options from config.yaml."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
                return cfg.get("execution", {})
        except Exception:
            pass
    return {}


EXEC_SETTINGS = load_execution_settings()
MAX_WORKERS = int(EXEC_SETTINGS.get("max_workers", 2))
SKIP_COMPLETED = bool(EXEC_SETTINGS.get("skip_completed", True))
TIMEOUT_SECONDS = int(EXEC_SETTINGS.get("timeout_seconds", 1800))

executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)


def find_summary_file(run_id: str) -> Optional[Path]:
    """Locate summary.json for a given run_id in the reports tree."""
    search_root = PROJECT_ROOT / "Backtest_Module" / "reports" / "experiments" / run_id
    if not search_root.exists():
        return None
    summaries = list(search_root.glob("**/summary.json"))
    if summaries:
        return summaries[0]
    return None


def copy_all_run_components(source_dir: Path, target_dir: Path):
    """Copy all components (CSV, HTML, JSON, TXT, subdirectories) produced for this run."""
    target_dir.mkdir(parents=True, exist_ok=True)
    for item in source_dir.iterdir():
        dst = target_dir / item.name
        try:
            if item.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)
        except Exception as e:
            logger.warning(f"Could not copy artifact {item.name}: {e}")


def run_and_archive(script_path: Path) -> bool:
    """
    Execute a single strategy script and manage staging and archiving with all components.
    """
    script_name = script_path.name
    run_id = script_path.stem
    staging_folder = BACKTEST_RESULTS_OUTBOX / run_id
    log_path = BACKTEST_LOGS / f"{run_id}.log"
    meta_path = script_path.with_suffix(".json")

    try:
        summary_file = find_summary_file(run_id)

        # 1. Check resume policy
        if SKIP_COMPLETED and summary_file and summary_file.exists() and summary_file.stat().st_size > 0:
            logger.info(f"[RESUME] Found existing valid summary for {run_id} at {summary_file}. Skipping execution.")
            staging_folder.mkdir(parents=True, exist_ok=True)
            # Copy all produced components from report directory
            copy_all_run_components(summary_file.parent, staging_folder)
            shutil.copy2(script_path, staging_folder / script_name)
            if meta_path.exists():
                shutil.copy2(meta_path, staging_folder / meta_path.name)

            # Move script to processed
            dest_script = SCRIPTS_PROCESSED / script_name
            shutil.move(str(script_path), str(dest_script))
            if meta_path.exists():
                shutil.move(str(meta_path), str(SCRIPTS_PROCESSED / meta_path.name))

            dest_results = BACKTEST_RESULTS_INBOX / run_id
            if dest_results.exists():
                shutil.rmtree(dest_results)
            shutil.move(str(staging_folder), str(dest_results))
            return True

        # 2. Prepare staging folder
        staging_folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(script_path, staging_folder / script_name)
        if meta_path.exists():
            shutil.copy2(meta_path, staging_folder / meta_path.name)

        # 3. Execute strategy script directly
        cmd = [sys.executable, str(script_path)]

        logger.info(f"Running backtest script: {script_name}")
        logger.info(f"Live log: {log_path}")

        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
        )

        with open(log_path, "w", encoding="utf-8") as lf:
            while True:
                line = proc.stdout.readline()
                if line == "" and proc.poll() is not None:
                    break
                if line:
                    lf.write(line)
                    lf.flush()
                    clean = line.strip()
                    if any(key in clean.lower() for key in ("cagr", "sharpe", "completed", "error", "traceback", "finished", "running")):
                        print(f"[{run_id}] {clean}")

        return_code = proc.wait(timeout=TIMEOUT_SECONDS)
        summary_file = find_summary_file(run_id)

        # 4. Check run outcome
        if return_code == 0 and summary_file and summary_file.exists():
            logger.info(f"Completed {run_id} successfully (exit code 0).")

            # Copy all generated components from Backtest_Module into staging
            copy_all_run_components(summary_file.parent, staging_folder)
            shutil.copy2(script_path, staging_folder / script_name)
            if meta_path.exists():
                shutil.copy2(meta_path, staging_folder / meta_path.name)

            # Move script to processed
            dest_script = SCRIPTS_PROCESSED / script_name
            shutil.move(str(script_path), str(dest_script))
            if meta_path.exists():
                shutil.move(str(meta_path), str(SCRIPTS_PROCESSED / meta_path.name))
                logger.info(f"Archived script -> {dest_script}")

            # Move staging folder to Backtest_Results/non_processed/
            dest_results = BACKTEST_RESULTS_INBOX / run_id
            if dest_results.exists():
                shutil.rmtree(dest_results)
            shutil.move(str(staging_folder), str(dest_results))
            logger.info(f"Moved all run results & components -> {dest_results}")
            return True
        else:
            logger.warning(f"Failed {run_id} (exit code {return_code}, summary found: {bool(summary_file)}).")
            if staging_folder.exists():
                shutil.rmtree(staging_folder, ignore_errors=True)

            error_dest = SCRIPTS_ERRONOUS / script_name
            if script_path.exists():
                shutil.move(str(script_path), str(error_dest))
                if meta_path.exists():
                    shutil.move(str(meta_path), str(SCRIPTS_ERRONOUS / meta_path.name))
                logger.info(f"Moved failed script -> {error_dest}")
            return False

    except Exception as e:
        logger.exception(f"Exception during run execution for {run_id}: {e}")
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n\n=== Runner Agent Exception ===\n{repr(e)}\n")

        if staging_folder.exists():
            shutil.rmtree(staging_folder, ignore_errors=True)

        error_dest = SCRIPTS_ERRONOUS / script_name
        if script_path.exists():
            try:
                shutil.move(str(script_path), str(error_dest))
                if meta_path.exists():
                    shutil.move(str(meta_path), str(SCRIPTS_ERRONOUS / meta_path.name))
            except Exception:
                pass
        return False


def run_all_pending() -> int:
    """Run all pending strategy scripts in outbox/non_processed/."""
    pending = list(SCRIPTS_INBOX.glob("*.py"))
    total = len(pending)
    logger.info(f"Found {total} pending strategy script(s) in {SCRIPTS_INBOX}")
    if not pending:
        return 0

    success_count = 0
    futures = [executor.submit(run_and_archive, p) for p in pending]
    for future in as_completed(futures):
        try:
            if future.result():
                success_count += 1
        except Exception as e:
            logger.exception(f"Task raised error: {e}")

    logger.info(f"Execution batch completed: {success_count}/{total} runs succeeded.")
    return success_count


if __name__ == "__main__":
    run_all_pending()
