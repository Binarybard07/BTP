# Research Log

## Entry 001: Pipeline Simplification & Baseline State
- **Date**: 2026-09-24
- **Topic**: Repository Cleanup & Base Pipeline Definition
- **Summary**: Outdated V1 research components (baseline cross-sectional signal ranker, equal-weight portfolio constructor, monthly rebalancer script) were removed.
- **Current Baseline Pipeline**: `CSVDataLoader` -> `DataValidator` -> `FeatureEngine` -> `Strategy`.
- **Next Research Step**: Evaluate EMA Crossover strategy mechanics in Nautilus-based framework.

## Entry 002: Multi-Factor XGBoost Regression & Monthly Walk-Forward Rebalancing Architecture
- **Date**: 2026-10-05
- **Topic**: Machine Learning Factor Strategy & Causal Monthly Rebalancing
- **Summary**: Transitioned from simple technical crossover strategies to a full machine-learning multi-factor cross-sectional alpha architecture.
  - **Feature Engine**: 6 technical factors spanning momentum (`rsi_14`, `roc_20`), trend (`macd_hist`, `close_to_ma50`, `close_to_ma200`), and volatility (`atr_14`).
  - **Target Engine**: 10-trading-day forward close return (`target_10d`) with negative shift ($t+10$), cleanly decoupling factor computation from target labeling.
  - **Lookahead Prevention**: Expanding-window walk-forward retraining model enforces a 10-day realization cutoff ($t_{\text{cutoff}} \le t_{\text{rebal}} - 10$ trading sessions). Historical bars whose 10-day forward returns have not realized by rebalance date $t_{\text{rebal}}$ are strictly excluded from the training split.
  - **Cross-Sectional Stock Selection**: Top 20 stocks ranked descending by predicted 10-day return via `TopNSelector`.
  - **Portfolio Construction**: Equal weighting ($1/N$, i.e. 5% per stock) across selected universe via `EqualWeightPortfolio`.
  - **Causal Monthly Scheduling**: Rebalance triggers operate causally on arrival of the first trading session in calendar month $M+1$, taking session $T$ close data from month $M$ as the decision point. No forward calendar scanning is performed.
  - **Execution Rebalancer**: Rebalancing layer reads current portfolio equity from NautilusTrader, sizes target quantities, enforces step/lot sizes via `instrument.make_qty()`, and executes sells prior to buys to preserve liquidity.
- **Current Pipeline**: `CSVDataLoader` -> `DataValidator` -> `FeatureEngine` -> `TargetEngine` -> `TrainingDatasetBuilder` -> `XGBoostRegressor` -> `TopNSelector` -> `EqualWeightPortfolio` -> `Rebalancer` (Nautilus).
- **Next Research Step**: Evaluate backtest equity curves, transaction cost sensitivity, and factor information coefficients (IC/Rank IC) across historical NIFTY 100 constituents.

