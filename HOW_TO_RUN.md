# How to Run the XGBoost Strategy

A complete guide for setting up the project from scratch, creating the virtual environment using `uv`, and running the XGBoost strategy for backtesting and paper-trading.

---

## 1. Prerequisites

Before starting, ensure you have the following installed on your machine:

1. **Git**: [git-scm.com](https://git-scm.com/)
2. **`uv`** (Fast Python package & project manager):
   - **Windows (PowerShell)**:
     ```powershell
     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
     ```
   - **Linux / macOS**:
     ```bash
     curl -LsSf https://astral.sh/uv/install.sh | sh
     ```
   *(Verify installation with `uv --version`)*

> [!NOTE]
> The project targets **Python 3.11** (configured in `.python-version` and `pyproject.toml`). `uv` manages and downloads Python 3.11 automatically without requiring manual system installs.

---

## 2. Clone the Repository

Clone the project repository and switch to the active branch (`qbeast`):

```bash
git clone https://github.com/Binarybard07/BTP.git
cd BTP
git checkout qbeast
```

---

## 3. Create Virtual Environment & Install Dependencies via `uv`

The project utilizes `uv` workspace management (`pyproject.toml`), which bundles the root package (`equity_strategy`) and the execution/backtest framework (`Backtest_Module` / `qbeast-backtest`) into a unified environment.

### Step 3.1: Install Python 3.11
```bash
uv python install 3.11
```

### Step 3.2: Create the Virtual Environment
```bash
uv venv --python 3.11
```

### Step 3.3: Sync Dependencies & Workspace Packages
```bash
uv sync
```

`uv sync` will automatically:
- Install all locked dependencies (`nautilus-trader`, `xgboost`, `pandas`, `scikit-learn`, `plotly`, `pyyaml`, etc.).
- Link `src/equity_strategy` and `Backtest_Module` in editable mode (`workspace: true`).

*(Optional)* To activate the virtual environment in your current shell:
- **Windows (PowerShell)**:
  ```powershell
  .venv\Scripts\Activate.ps1
  ```
  *(If script execution is disabled, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first)*
- **Linux / macOS / Bash**:
  ```bash
  source .venv/bin/activate
  ```

---

## 4. Environment Configuration (`.env`)

Copy the template configuration into `.env`:

- **Windows PowerShell**:
  ```powershell
  Copy-Item .env.example .env
  ```
- **Linux / macOS / Git Bash**:
  ```bash
  cp .env.example .env
  ```

### Key Configuration Parameters in `.env`:
```env
# Universe preset: nifty200_ddq, nifty50_ddq, nifty500_ddq, etc.
QBEAST_BACKTEST_UNIVERSE=nifty200_ddq

# Date window (YYYY-MM-DD)
QBEAST_BACKTEST_START=2019-01-01
QBEAST_BACKTEST_END=2026-10-19

# Rebalance schedule
REBALANCE_FREQ=monthly

# Data directories (relative to project root)
QBEAST_DATA_ROOT=data/Final_With_Survivorship_Data
QBEAST_MEMBERSHIP_ROOT=data/Survivorship_Bias

# Bypass engine preflight check outside containers
REGIMESWITCH_SKIP_PREFLIGHT=1
```

---

## 5. Running the XGBoost Strategy

You can execute the strategy through either the direct script runner, the pipeline CLI, or the live paper-trading daemon:

### Method A: Direct Strategy Runner (NautilusTrader Engine)
Runs the end-to-end backtest on the official NautilusTrader execution harness:

```powershell
uv run python Backtest_Module/src/qbeast_backtest/stage2_strategy/xgboost_regressor_strategy.py
```

### Method B: Pipeline CLI Entrypoint
Allows selecting between the certified NautilusTrader engine and the high-speed internal simulation ledger:

- **NautilusTrader Engine**:
  ```powershell
  uv run python Backtest_Module/src/qbeast_backtest/pipeline.py --strategy xgboost_regressor --engine nautilus
  ```
- **Fast Internal Ledger** (ideal for rapid feature/parameter sweeps):
  ```powershell
  uv run python Backtest_Module/src/qbeast_backtest/pipeline.py --strategy xgboost_regressor --engine internal
  ```

### Method C: OpenAlgo Paper-Trading / Live Execution Daemon
To run the automated trading daemon that computes evening model signals and submits market orders at 9:15 AM IST:

```powershell
uv run python openalgo_xgboost_strategy.py
```

---

## 6. Current Strategy Parameters & Architecture

The strategy is defined in [`Backtest_Module/src/qbeast_backtest/stage2_strategy/xgboost_regressor_strategy.py`](file:///c:/Users/Jenish/Desktop/SEM%20VII/BTP/Backtest_Module/src/qbeast_backtest/stage2_strategy/xgboost_regressor_strategy.py).

### Core Parameter Specification:
- **Universe**: `nifty200_ddq` (survivorship-bias free).
- **Model**: `XGBoostRegressor`
  - `n_estimators`: `100`
  - `max_depth`: `3`
  - `learning_rate`: `0.05`
  - `tree_method`: `"hist"`
  - `subsample`: `0.8`
  - `colsample_bytree`: `0.8`
  - `random_state`: `42`
- **Training Window**: Expanding-window walk-forward with `min_train_days=1260` (~5 years of trading data).
- **Target Variable**: `target_fwd_return_63` (Forward 63-day return / ~3 months).
- **Feature Set (16 Modular Features)**:
  - **Trend**: `close_to_ema20`, `close_to_ema50`, `close_to_sma20`, `close_to_sma50`, `close_to_sma100`, `close_to_sma200`
  - **Momentum & Returns**: `return_5`, `return_10`, `return_21`, `return_63`, `return_126`, `return_252`, `roc_21`
  - **Volatility**: `volatility_10`, `volatility_21`, `volatility_63`
- **Selection**: `TopNSelector(n=10)` (selects the 10 highest-ranked symbols).
- **Portfolio Weighting**: `EqualWeightPortfolio` (10% per selected symbol).
- **Risk Management**: `FixedPercentageRiskManager(max_deployment_pct=0.95)` (95% capital deployed, 5% cash reserve).
- **Rebalance Frequency**: `monthly`

---

## 7. Output Deliverables & Reports

After each backtest execution, results are generated and stored in:
`Backtest_Module/reports/<UNIVERSE>/` (e.g. `Backtest_Module/reports/nifty200_ddq/`):

1. **`dashboard.html`**: Interactive Plotly dashboard showing NAV growth, benchmark comparison, rolling Sharpe, monthly returns heatmap, and underwater drawdown curves.
2. **`summary.json`**: Performance statistics (CAGR, Sharpe, Calmar, Max Drawdown, Turnovers, STT/Charges, Net P&L).
3. **`equity.csv`**: Daily NAV series, cash balance, exposure, and portfolio valuation.
4. **`tradebook_backtest.csv`**: Granular transaction ledger containing every fill price, quantity, slippage, and statutory cost.

---

## 8. Verifying Environment & Running Self-Tests

To ensure all contracts and mathematical execution modules operate correctly on your machine:

```powershell
# 1. Run the Backtest Module contract conformance self-test
uv run qbeast-backtest-selftest

# 2. Run the certified execution and cost math arithmetic suite
uv run qbeast-exec-selftest

# 3. Run all unit tests
uv run pytest
```
