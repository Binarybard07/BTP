#!/usr/bin/env python3
"""
Experiment Lab - Orchestrator Entrypoint
========================================
Runs all 4 agents concurrently and in parallel:
  1. generator_agent: Processes pending templates, then observes for new ones
  2. runner_agent: Processes pending combo scripts, then observes for new ones
  3. result_agent: Processes pending backtest results, then observes for new ones
  4. google_sheets: Synchronizes existing metrics.xlsx, then observes for new changes

All agents run independently in parallel, or each can be launched directly on its own.
Usage:
  python run_lab.py
"""

import logging
from pathlib import Path
import subprocess
import sys
import time

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run_lab")


def main():
    agents = [
        ("generator_agent", BASE_DIR / "generator_agent.py"),
        ("runner_agent", BASE_DIR / "runner_agent.py"),
        ("result_agent", BASE_DIR / "result_agent.py"),
        ("google_sheets", BASE_DIR / "google_sheets.py"),
    ]

    procs = []
    logger.info("==================================================================")
    logger.info("  Launching all 4 Experiment Lab agents in parallel (autonomous)  ")
    logger.info("==================================================================")

    for name, script in agents:
        logger.info(f"Starting background agent: {name} ({script.name})...")
        p = subprocess.Popen(
            [sys.executable, "-u", str(script)],
            cwd=str(BASE_DIR),
        )
        procs.append((name, p))

    logger.info("All 4 agents are actively running in parallel.")
    logger.info("Press Ctrl+C to stop all agents cleanly.")

    try:
        while True:
            time.sleep(1)
            # Check if any process exited unexpectedly
            for name, p in procs:
                ret = p.poll()
                if ret is not None and ret != 0:
                    logger.warning(f"Agent '{name}' exited unexpectedly with code {ret}")
    except KeyboardInterrupt:
        logger.info("\nShutdown signal received. Stopping all agents...")
        for name, p in procs:
            if p.poll() is None:
                logger.info(f"Terminating {name} (PID {p.pid})...")
                p.terminate()
        for name, p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        logger.info("All agents stopped.")


if __name__ == "__main__":
    main()
