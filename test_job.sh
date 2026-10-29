#!/bin/bash
#SBATCH --job-name=btp_test
#SBATCH --partition=test          # 'test' partition
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2         # 2 CPU cores
#SBATCH --mem=8gb                 # 8 GB memory limit
#SBATCH --time=00:10:00           # 10 minutes max
#SBATCH --output=test_%j.log      # Log directly in current folder

echo "==================== SLURM JOB START ===================="
date
echo "Node: $(hostname)"
echo "Working directory: $(pwd)"
echo "Job ID: $SLURM_JOB_ID"
echo "========================================================="

# 1. Correct project paths
PROJECT_DIR="/scratch/data/p23et0001/jenish/BTP"
CONTAINER="${PROJECT_DIR}/container.sif"
PYTHON_ENV="${PROJECT_DIR}/.venv/bin/python"
BIND_PATH="/scratch/data/p23et0001:/scratch/data/p23et0001"

cd "${PROJECT_DIR}"

# 2. Load Singularity environment module
module load singularity/3.7.1 2>/dev/null || module load singularity 2>/dev/null || true

# 3. Environment check
echo ">> Checking python and library imports..."
singularity exec --bind "${BIND_PATH}" "${CONTAINER}" "${PYTHON_ENV}" -c \
    "import sys, nautilus_trader, xgboost; print(f'Python: {sys.version.split()[0]} | Nautilus: {nautilus_trader.__version__} | XGBoost: {xgboost.__version__}')"

# 4. Run a fast test backtest (6-month slice on nifty50)
echo ">> Running test backtest of XGBoost Strategy..."
export QBEAST_BACKTEST_START="2023-01-01"
export QBEAST_BACKTEST_END="2023-06-30"
export QBEAST_BACKTEST_UNIVERSE="nifty50"

singularity exec --bind "${BIND_PATH}" "${CONTAINER}" \
    "${PYTHON_ENV}" Backtest_Module/src/qbeast_backtest/stage2_strategy/xgboost_regressor_strategy.py

echo "==================== SLURM JOB FINISHED ================="
date