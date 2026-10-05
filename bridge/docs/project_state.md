# Project State

- **Current Project Phase**: Complete Multi-Factor ML Strategy Pipeline (XGBoost Regressor) and Nautilus Execution Rebalancer Layer implemented.
- **End-to-End Pipeline**:
  - **Data Ingestion & Validation**: `CSVDataLoader` (independent per-CSV strict validation skipping corrupted files, missing-value preservation) -> `DataValidator` (OHLCV integrity, schema, chronological monotonicity).
  - **Feature Engineering**: `FeatureEngine` (Momentum: RSI, ROC; Trend: MACD, MA Ratios; Volatility: ATR, Bollinger Bands).
  - **Target Calculation**: `TargetEngine` (10-trading-day forward close return `target_10d`).
  - **Dataset Building**: `TrainingDatasetBuilder` (combines OHLCV, features, and target, cleanly dropping lookahead and trailing NaNs).
  - **Machine Learning Layer**: `BaseModel` contract -> `XGBoostRegressor` (walk-forward expanding window training with target realization cutoff).
  - **Cross-Sectional Stock Selection**: `BaseSelector` contract -> `TopNSelector` (Top 20 stocks by score), `ThresholdSelector`, `PercentileSelector`.
  - **Portfolio Construction**: `BasePortfolio` contract -> `EqualWeightPortfolio` ($1/N$ capital allocation).
  - **Causal Rebalance Scheduling**: `BaseRebalanceStrategy` -> `MonthlyRebalanceStrategy` (causal calendar-month transition detection at portfolio level on session $T+1$ identifying session $T$ close as decision date).
  - **Execution Rebalancing**: `Rebalancer` (Nautilus execution-side rebalancer: reads authoritative portfolio equity from Nautilus, sizes target quantities, closes/reduces exiting positions, enforces instrument lot size and precision constraints, generates/submits Market orders with sells sequenced before buys).
  - **Strategy Orchestrator**: `XGBoostRegressorStrategy` (end-to-end research orchestrator producing `StrategyResult`, serializable via `.pkl` and human-readable `.txt`).
- **Nautilus Trader Integration**:
  - `NautilusDataConverter`: Converts OHLCV into native Nautilus `Equity`, `BarType`, and daily `Bar` objects (15:30 IST market close).
  - `MACrossoverStrategy`: Concrete Nautilus `Strategy` implementation running 50/200 MA crossover on `BacktestEngine`.
  - `Rebalancer`: Execution bridge operating directly on Nautilus `Portfolio`, `Cache`, and `OrderFactory`.
- **Environment & Dependencies**: Python 3.12 managed via UV (`uv.lock`), NautilusTrader 2.0.0rc5, XGBoost, pandas, numpy. Centralized logging (`configure_logging`) active across all modules.
- **Next Implementation Step**: Create an event-driven Nautilus `Strategy` subclass (e.g. `XGBoostNautilusStrategy`) that connects `MonthlyRebalanceStrategy` and `Rebalancer` into `BacktestEngine` for native backtesting.
