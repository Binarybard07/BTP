# How to Run the XGBoost Strategy

A minimal guide for running the XGBoost strategy and modifying its configurations.

---

## 1. Quick Run

From the project root:

```powershell
uv run python Backtest_Module/src/qbeast_backtest/stage2_strategy/xgboost_regressor_strategy.py
```

Or through the pipeline CLI:

```powershell
uv run python Backtest_Module/src/qbeast_backtest/pipeline.py --strategy xgboost_regressor --engine internal
```

---

## 2. Changing Run-Time Configurations

You can configure the universe, date range, and data path using environment variables before running:

### Windows PowerShell
```powershell
# Set test window (e.g. 2-year slice)
$env:QBEAST_BACKTEST_START = "2023-01-01"
$env:QBEAST_BACKTEST_END   = "2024-12-31"

# Choose universe preset (e.g. nifty100, midcap150, smallcap250)
$env:QBEAST_BACKTEST_UNIVERSE = "nifty100"

# Optional: override data directory if not in default location
$env:QBEAST_DATA_ROOT = "$PWD\data\Final_Without_Survivorship_Data"

# Run
uv run python Backtest_Module/src/qbeast_backtest/stage2_strategy/xgboost_regressor_strategy.py
```

### Command Prompt (cmd.exe)
```cmd
set QBEAST_BACKTEST_START=2023-01-01
set QBEAST_BACKTEST_END=2024-12-31
set QBEAST_BACKTEST_UNIVERSE=nifty100
uv run python Backtest_Module/src/qbeast_backtest/stage2_strategy/xgboost_regressor_strategy.py
```

---

## 3. Changing Strategy Parameters

Open [`Backtest_Module/src/qbeast_backtest/stage2_strategy/xgboost_regressor_strategy.py`](file:///c:/Users/Jenish/Desktop/SEM%20VII/BTP/Backtest_Module/src/qbeast_backtest/stage2_strategy/xgboost_regressor_strategy.py) and modify `strategy_params` inside `main()`:

```python
strategy_params = {
    # Rebalance schedule: "monthly"
    "rebalance_frequency": "monthly",

    # Stock selection: "percentile" (selects top 100 - percentile %)
    "selector": "percentile",
    "percentile": 85.0,            # 85.0 selects top 15% quantile

    # Weighting: "equal"
    "weighting": "equal",

    # Capital deployment limit (0.75 = 75% invested, 25% cash buffer)
    "max_deployment_pct": 0.75,

    # Warmup and target parameters
    "min_train_days": 252,          # Minimum trading days required before first fit
    "target_horizon": 10,           # Forward return target horizon (days)
}
```

### Tuning XGBoost Hyperparameters
To adjust the regressor hyperparameters, pass them to `XGBoostRegressorStrategy(...)`:

```python
strat = XGBoostRegressorStrategy(
    ...,
    n_estimators=100,              # Number of trees (default: 100)
    max_depth=3,                   # Tree depth (default: 3)
    learning_rate=0.05,            # Step size shrinkage (default: 0.05)
    subsample=0.8,                 # Row subsampling ratio (default: 0.8)
    colsample_bytree=0.8,          # Column subsampling ratio (default: 0.8)
    random_state=42,
)
```

---

## 4. Selecting Execution Engine

Pass `--engine` to choose between execution engines:

- **`internal`** (Fast ledger simulation):
  ```powershell
  uv run python Backtest_Module/src/qbeast_backtest/pipeline.py --strategy xgboost_regressor --engine internal
  ```
- **`nautilus`** (NautilusTrader order/fill engine):
  ```powershell
  uv run python Backtest_Module/src/qbeast_backtest/pipeline.py --strategy xgboost_regressor --engine nautilus
  ```

---

## 5. Viewing Generated Reports

After a run finishes, reports are written to:
`Backtest_Module/reports/<UNIVERSE>/`

- **`dashboard.html`**: Open in browser for interactive performance and drawdown charts.
- **`equity.csv`**: Daily NAV and post-tax account curve.
- **`summary.json`**: Performance metrics (CAGR, Sharpe, Max Drawdown, frictions, tax).
- **`tradebook_backtest.csv`**: Detailed log of every executed fill.
