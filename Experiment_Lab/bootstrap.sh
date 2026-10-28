#!/usr/bin/env bash
# ==============================================================================
# Experiment Lab - Multi-Agent Bootstrap Launcher
# Mirrors RM_TM_TF_Combo bootstrap.sh architecture for background agent sessions.
# ==============================================================================

set -euo pipefail

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${LAB_DIR}/../.backtest_module/bin/python"

if [ ! -f "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

start_screen_session() {
  local session="$1"
  local script="$2"

  if screen -ls | grep -q "[[:space:]]${session}[[:space:]]"; then
    echo ">> Screen session '${session}' is already running - skipping."
    return
  fi

  echo ">> Starting screen session '${session}' -> ${script}"
  screen -dmS "${session}" bash -lc \
    "cd '${LAB_DIR}' && exec ${PYTHON_BIN} -u '${script}' || { echo 'FAILED to run ${session}'; sleep 30; }"
}

echo "=========================================================="
echo "          Launching Experiment Lab Watcher Agents         "
echo "=========================================================="

start_screen_session "lab_generator" "generator_agent.py"
start_screen_session "lab_runner"    "runner_agent.py"
start_screen_session "lab_results"   "result_agent.py"
start_screen_session "lab_sheets"    "google_sheets.py"

echo "=========================================================="
echo "All Experiment Lab agent sessions initiated."
echo "Use 'screen -ls' to inspect running agents."
echo "=========================================================="
