# Experiment Registry

## Summary
The experiment registry tracks empirical strategy runs, parameter sweeps, and execution evaluations across the BTP quantitative pipeline.

---

## Completed Experiments

### EXP-001: Nautilus MA Crossover Strategy Baseline Verification
- **Status**: Completed
- **Date**: 2026-09-24
- **Strategy**: `MACrossoverStrategy`
- **Data Source**: Historical EoD data (`ABB.NSE`, 5,786 bars)
- **Parameters**: 50-day / 200-day EMA trend crossover
- **Summary**: Verified end-to-end integration of `CSVDataLoader` -> `FeatureEngine` -> `NautilusDataConverter` -> `MACrossoverStrategy` with native NautilusTrader 2.0.0rc5 engine. Verified bar timestamps (15:30 IST market close) and signal order submission.

### EXP-002: Multi-Factor XGBoost Regressor Walk-Forward Strategy
- **Status**: Completed (Research Pipeline Run)
- **Date**: 2026-10-05
- **Strategy**: `XGBoostRegressorStrategy`
- **Data Universe**: NIFTY 100 EoD dataset (filtered by strict per-file validation)
- **Features**: 6 technical factors (`rsi_14`, `roc_20`, `macd_hist`, `close_to_ma50`, `close_to_ma200`, `atr_14`)
- **Target**: 10-trading-day forward close return (`target_10d`)
- **Training Scheme**: Expanding-window walk-forward retraining with strict target realization cutoff ($t_{\text{cutoff}} \le t_{\text{rebal}} - 10$ trading days)
- **Selection**: `TopNSelector` ($N=20$)
- **Portfolio Construction**: `EqualWeightPortfolio` ($w_i = 1/20 = 5\%$)
- **Rebalance Frequency**: Causal monthly rebalancing
- **Artifacts Generated**:
  - Full lossless result object: `output/strategy_result/strategy_result.pkl`
  - Human-readable summary report: `output/strategy_result/strategy_result.txt`
- **Summary**: Validated complete research-level ML pipeline without future lookahead bias, computing walk-forward factor predictions, cross-sectional rankings, selected tickers, and target weights across all monthly rebalance periods.

---

## Upcoming Experiments

- **EXP-003 (Proposed)**: Full Event-Driven Nautilus Backtest of XGBoost Regressor Strategy with `Rebalancer` on NIFTY 100 EoD data.
  - **Objective**: Execute target weight rebalancing within Nautilus `BacktestEngine`, simulating live portfolio equity tracking, order sizing, lot-size rounding, and realistic transaction cost / slippage friction.

