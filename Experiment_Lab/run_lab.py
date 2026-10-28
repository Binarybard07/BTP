#!/usr/bin/env python3
"""
Experiment Lab - Orchestrator Entrypoint
========================================
Executes the full strategy experimentation pipeline directly with no flags needed:
  1. generator_agent: Generates strategy scripts from templates into outbox/non_processed/
  2. runner_agent: Executes pending backtest scripts through Backtest_Module
  3. result_agent: Parses summary.json files, calculates scores, and updates metrics.xlsx
  4. google_sheets: Synchronizes metrics.xlsx to Google Sheets
  5. Displays current queue and metrics status report

Usage:
  python run_lab.py
"""

import logging
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import generator_agent
import runner_agent
import result_agent
import google_sheets

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_lab")


def show_status():
    """Print status of experiment queues, logs, and metrics."""
    outbox_pending = list((BASE_DIR / "outbox" / "non_processed").glob("*.py"))
    outbox_processed = list((BASE_DIR / "outbox" / "processed").glob("*.py"))
    outbox_error = list((BASE_DIR / "outbox" / "Errornous_scripts").glob("*.py"))

    results_pending = [d for d in (BASE_DIR / "Backtest_Results" / "non_processed").iterdir() if d.is_dir()]
    results_processed = [d for d in (BASE_DIR / "Backtest_Results" / "processed").iterdir() if d.is_dir()]
    metrics_file = BASE_DIR / "Backtest_Results" / "metrics.xlsx"

    print("\n" + "=" * 65)
    print("                EXPERIMENT LAB STATUS REPORT")
    print("=" * 65)
    print(f" Strategy Scripts in Outbox Pending:   {len(outbox_pending)}")
    print(f" Strategy Scripts Processed:           {len(outbox_processed)}")
    print(f" Strategy Scripts Erroneous / Failed:  {len(outbox_error)}")
    print("-" * 65)
    print(f" Backtest Results Pending Parse:       {len(results_pending)}")
    print(f" Backtest Results Processed:           {len(results_processed)}")
    print(f" Metrics Excel Exists:                 {metrics_file.exists()} ({metrics_file})")
    print("=" * 65 + "\n")


def run():
    """Execute complete end-to-end experiment pipeline."""
    logger.info(">>> STEP 1: GENERATING STRATEGY SCRIPTS FROM TEMPLATES <<<")
    generator_agent.generate_all()

    logger.info(">>> STEP 2: EXECUTING BACKTEST SCRIPTS <<<")
    runner_agent.run_all_pending()

    logger.info(">>> STEP 3: PARSING RESULTS & UPDATING METRICS.XLSX <<<")
    result_agent.process_all_pending()

    logger.info(">>> STEP 4: SYNCHRONIZING TO GOOGLE SHEETS <<<")
    google_sheets.sync_metrics_to_sheets()

    show_status()
    logger.info("Experiment Lab execution completed successfully.")


if __name__ == "__main__":
    run()
