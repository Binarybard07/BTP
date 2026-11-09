#!/bin/bash
#SBATCH --job-name=btp_grid16
#SBATCH --partition=fat         # 'small', 'medium', 'large', 'fat' or 'test'
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=20         # 20 CPU cores (16 worker tasks + 4 orchestrator agents)
#SBATCH --mem=64gb                 # 64 GB memory limit (prevents SLURM OOM kill on 16 parallel workers)
#SBATCH --time=2-00:00:00           # 2 days, 0 hours, 0 minutes, 0 seconds maximum
#SBATCH --output=single_%j.log    # Slurm log file

echo "==================== SLURM SINGLE JOB START ===================="
date
echo "Node: $(hostname)"
echo "Working directory: $(pwd)"
echo "Job ID: $SLURM_JOB_ID"
echo "================================================================"

PROJECT_DIR="/scratch/data/p23et0001/jenish/BTP"
CONTAINER="${PROJECT_DIR}/container.sif"
PYTHON_ENV="${PROJECT_DIR}/.venv/bin/python"
BIND_PATH="/scratch/data/p23et0001:/scratch/data/p23et0001"
SCRIPT_PATH="Experiment_Lab/run_lab.py"

cd "${PROJECT_DIR}"

module load singularity/3.7.1 2>/dev/null || module load singularity 2>/dev/null || true

echo ">> Running script: ${SCRIPT_PATH}"

singularity exec --bind "${BIND_PATH}" "${CONTAINER}" \
    "${PYTHON_ENV}" "${SCRIPT_PATH}"

echo "==================== SLURM SINGLE JOB FINISHED ================"
date