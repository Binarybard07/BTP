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


## Entry 003: Event-Driven Nautilus Backtesting, Execution Friction, Capital-Deployment Budgeting, and Roadmap Progression
- **Date**: 2026-10-18
- **Topic**: Realistic Execution Friction, Capital-Deployment Budgeting, and Structured Implementation Progression
- **Summary**: Progressed from standalone factor simulation to full event-driven multi-instrument execution within NautilusTrader 2.0.0rc5 `BacktestEngine`.
  - **Friction Models**: Implemented statutory Indian cash equity delivery fees (`IndianEquityDeliveryFeeModel`) including STT, turnover charges, SEBI fees, stamp duty, GST, and daily DP sell fees. Added adverse tick slippage simulation (`SlippageFillModel`).
  - **Capital-Deployment Risk Management**: Established modular capital deployment budgeting (`BaseRiskManager`, `FixedPercentageRiskManager`), sizing positions against allowable deployment equity. Integrated target weight validation ($w_i \ge 0.0, \sum w_i \le 1.0001$) in `Rebalancer`. This completes risk management for current project objectives; stop-losses, take-profits, and drawdown halts are intentionally out of scope.
  - **Execution Timing**: Formalized month-end trading session $T$ close on `on_bar()` as the sole rebalance trigger, submitting market orders that fill against $T+1$ opening quotes. Lookahead is prevented with strict target realization cutoffs.
  - **Reporting Simplification**: Removed custom recorder plumbing and decoupled telemetry state from the strategy execution flow, standardizing on native NautilusTrader artifacts (`orders.csv`, `positions.csv`, `account.csv`, `stats.csv`, `returns_series.csv`) and `tearsheet.html` via `NautilusExporter`.
  - **Structured Progression**: Established a strict sequential roadmap:
    1. Risk management — Complete for current scope.
    2. Trade Management module — Next.
    3. Tax implementation.
    4. Survivorship-bias handling.
    5. OpenStatz tearsheet.
    6. Dashboard.
- **Current Pipeline**: `CSVDataLoader` -> `DataValidator` -> `FeatureEngine` -> `TargetEngine` -> `TrainingDatasetBuilder` -> `XGBoostRegressor` -> `TopNSelector` -> `EqualWeightPortfolio` -> `BaseRiskManager` / `FixedPercentageRiskManager` -> `Rebalancer` (Nautilus with `IndianEquityDeliveryFeeModel` & `SlippageFillModel`) -> `NautilusExporter`.
- **Next Research Step**: Inspect existing trade and order management mechanisms in `Rebalancer` and `XGBoostRegressorStrategy` to prepare requirements for the upcoming Trade Management module without duplicating functionality or modifying established execution behavior.
