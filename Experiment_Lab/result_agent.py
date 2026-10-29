#!/usr/bin/env python3
"""
Experiment Lab - Result Agent
=============================
Parses Backtest_Module results from summary.json, ranks runs by composite Score,
generates a single clean 'Metrics' sheet in metrics.xlsx, and archives all run components.

Scoring Formula:
  Score = 0.5 * (CAGR - Benchmark_CAGR) + 0.5 * (MaxDD - Benchmark_MaxDD)

Direct execution without flags: `python result_agent.py`
"""

import json
import logging
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Dict, List, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill
import pandas as pd
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ─────────────────────────────────────────────
# PATHS CONFIGURATION
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
CONFIG_FILE = BASE_DIR / "config.yaml"
RESULT_LOG_FILE = BASE_DIR / "result_agent.log"

BACKTEST_DIR = BASE_DIR / "Backtest_Results"
NEW_DIR = BACKTEST_DIR / "non_processed"
DONE_DIR = BACKTEST_DIR / "processed"
OUTPUT_XLSX = BACKTEST_DIR / "metrics.xlsx"

for d in (BACKTEST_DIR, NEW_DIR, DONE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────────
logger = logging.getLogger("result_agent")
logger.setLevel(logging.INFO)

if not logger.handlers:
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    fh = logging.FileHandler(RESULT_LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.INFO)

    fmt = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    ch.setFormatter(fmt)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)

METRIC_COLUMNS = [
    "Run_ID",
    "Score",
    "CAGR [%]",
    "Max Drawdown [%]",
    "Avg Drawdown [%]",
    "Sharpe Ratio",
    "Calmar Ratio",
    "Benchmark",
    "Benchmark CAGR [%]",
    "Benchmark MaxDD [%]",
    "Benchmark AvgDD [%]",
    "Benchmark Sharpe",
    "Window_Start",
    "Window_End",
    "Universe",
    "Survivorship",
    "Summary_Path",
    "Report_Folder",
]


def extract_run_data(folder_path: Path) -> Optional[Dict[str, Any]]:
    """Extract metrics from summary.json and metadata JSON inside folder."""
    summary_path = folder_path / "summary.json"
    if not summary_path.exists() or summary_path.stat().st_size == 0:
        logger.warning(f"No valid summary.json found in {folder_path.name}")
        return None

    try:
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)
    except Exception as e:
        logger.error(f"Error parsing {summary_path}: {e}")
        return None

    meta_files = [p for p in folder_path.glob("*.json") if p.name != "summary.json"]
    meta_data = {}
    if meta_files:
        try:
            with open(meta_files[0], "r", encoding="utf-8") as f:
                meta_data = json.load(f) or {}
        except Exception:
            pass

    cagr = float(summary.get("cagr", 0.0))
    maxdd = float(summary.get("maxdd", 0.0))
    bench_cagr = float(summary.get("benchmark_cagr", 0.0))
    bench_maxdd = float(summary.get("benchmark_maxdd", 0.0))
    score = round(0.5 * (cagr - bench_cagr) + 0.5 * (maxdd - bench_maxdd), 4)

    data = {
        "Run_ID": folder_path.name,
        "Score": score,
        "CAGR [%]": round(cagr, 3),
        "Max Drawdown [%]": round(maxdd, 3),
        "Avg Drawdown [%]": round(float(summary.get("avgdd", 0.0)), 3),
        "Sharpe Ratio": round(float(summary.get("sharpe", 0.0)), 3),
        "Calmar Ratio": round(float(summary.get("calmar", 0.0)), 3),
        "Benchmark": summary.get("benchmark_name", "N/A"),
        "Benchmark CAGR [%]": round(bench_cagr, 3),
        "Benchmark MaxDD [%]": round(bench_maxdd, 3),
        "Benchmark AvgDD [%]": round(float(summary.get("benchmark_avgdd", 0.0)), 3),
        "Benchmark Sharpe": round(float(summary.get("benchmark_sharpe", 0.0)), 3),
        "Window_Start": str(summary.get("window_start", meta_data.get("start_date", ""))),
        "Window_End": str(summary.get("window_end", meta_data.get("end_date", ""))),
        "Universe": summary.get("universe", meta_data.get("universe", "nifty500_ddq")),
        "Survivorship": bool(summary.get("survivorship", False)),
        "Summary_Path": str(summary_path),
        "Report_Folder": str(folder_path),
    }
    return data


def load_existing_df() -> pd.DataFrame:
    """Load existing metrics from OUTPUT_XLSX or return empty DataFrame."""
    if OUTPUT_XLSX.exists():
        try:
            df = pd.read_excel(OUTPUT_XLSX, sheet_name="Metrics", engine="openpyxl")
            for c in METRIC_COLUMNS:
                if c not in df.columns:
                    df[c] = pd.NA
            return df[METRIC_COLUMNS]
        except Exception as e:
            logger.warning(f"Could not read existing {OUTPUT_XLSX}: {e}")
    return pd.DataFrame(columns=METRIC_COLUMNS)


def write_styled_excel(df: pd.DataFrame):
    """Save metrics to a single clean 'Metrics' sheet with RM_TM_TF_Combo formatting."""
    if df.empty:
        return

    df = df.drop_duplicates(subset=["Run_ID"], keep="last")
    df_metrics = df.sort_values("Score", ascending=False).reset_index(drop=True)

    try:
        with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
            df_metrics.to_excel(writer, sheet_name="Metrics", index=False)
            ws_metrics = writer.sheets["Metrics"]
            format_worksheet(ws_metrics, df_metrics)

        logger.info(f"Saved metrics to {OUTPUT_XLSX} ('Metrics': {len(df_metrics)} rows)")
    except Exception as e:
        logger.exception(f"Failed writing Excel {OUTPUT_XLSX}: {e}")


def format_worksheet(ws, df: pd.DataFrame):
    """Freeze row 1, bold header, and highlight rows with Score > 0 in light green (#C6EFCE)."""
    ws.freeze_panes = "A2"
    header_font = Font(bold=True)
    for cell in ws[1]:
        cell.font = header_font

    green_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    try:
        score_idx = df.columns.get_loc("Score") + 1
        for r_idx in range(2, len(df) + 2):
            val = ws.cell(row=r_idx, column=score_idx).value
            if val is not None:
                try:
                    if float(val) > 0.0:
                        for col_idx in range(1, len(df.columns) + 1):
                            ws.cell(row=r_idx, column=col_idx).fill = green_fill
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass


class BatchHandler(FileSystemEventHandler):
    """Watches Backtest_Results/non_processed/ for new run output folders."""

    def __init__(self):
        super().__init__()
        self._processed = set()

    def on_created(self, event):
        if not event.is_directory:
            return
        folder_path = Path(event.src_path)
        if folder_path.name.startswith("."):
            return
        if folder_path.name in self._processed:
            return

        time.sleep(1.0)  # Allow folder and file copies to settle
        self.process_batch(folder_path)

    def process_batch(self, folder_path: Path) -> bool:
        if not folder_path.exists() or not folder_path.is_dir():
            return False
        if folder_path.name in self._processed:
            return False

        logger.info(f"Processing results folder: {folder_path.name}")
        try:
            data = extract_run_data(folder_path)
            if not data:
                logger.warning(f"No valid run data extracted from {folder_path.name}")
                return False

            df_master = load_existing_df()
            df_new = pd.DataFrame([data])
            df_updated = pd.concat([df_master, df_new], ignore_index=True)
            write_styled_excel(df_updated)

            dest = DONE_DIR / folder_path.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(folder_path), str(dest))
            self._processed.add(folder_path.name)
            logger.info(f"Archived all run components -> {dest}")
            return True
        except Exception as e:
            logger.exception(f"Error processing batch {folder_path.name}: {e}")
            return False


def bootstrap_existing(handler: BatchHandler):
    """Process any folders already in Backtest_Results/non_processed/ at startup in batch."""
    subdirs = sorted([p for p in NEW_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")])
    if not subdirs:
        logger.info(f"No pending results folders in {NEW_DIR}")
        return

    logger.info(f"Bootstrapping {len(subdirs)} existing results folder(s)...")
    df_master = load_existing_df()
    new_rows = []

    for folder in subdirs:
        try:
            data = extract_run_data(folder)
            if data:
                new_rows.append(data)
                dest = DONE_DIR / folder.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.move(str(folder), str(dest))
                handler._processed.add(folder.name)
                logger.info(f"Archived all run components -> {dest}")
        except Exception as e:
            logger.exception(f"Error bootstrapping {folder.name}: {e}")

    if new_rows:
        df_new = pd.DataFrame(new_rows)
        df_updated = pd.concat([df_master, df_new], ignore_index=True)
        write_styled_excel(df_updated)


def process_all_pending() -> int:
    """Process all completed runs waiting in Backtest_Results/non_processed/ (one-pass)."""
    handler = BatchHandler()
    bootstrap_existing(handler)
    return len(handler._processed)


def main():
    """Main result agent entrypoint: process pending folders first, then observe for new ones."""
    logger.info("Result-agent starting up...")
    handler = BatchHandler()
    bootstrap_existing(handler)

    logger.info(f"Watching for new backtest folders in {NEW_DIR}/ ...")
    observer = Observer()
    observer.schedule(handler, str(NEW_DIR), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutdown requested, stopping result agent.")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
