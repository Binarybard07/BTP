# Project State

- **Current Project Phase**: Event-driven multi-instrument XGBoost quantitative equity strategy integrated with NautilusTrader 2.0.0rc5, realistic Indian equity execution friction (statutory delivery fees & adverse tick slippage), capital-deployment risk management, and native Nautilus performance reporting/tearsheets.

- **End-to-End Pipeline Overview**:
  1. **Data Ingestion & Validation**:
     - `CSVDataLoader`: Discovers and loads daily OHLCV CSVs per symbol. Performs strict independent per-file validation; corrupted or invalid files are logged as warnings and skipped without crashing the universe load. Preserves missing data as `NaN` (no silent string coercion).
     - `DataValidator`: Enforces schema integrity, valid price/volume positivity, monotonic chronological ordering, and duplicate timestamp detection.
     - `StrategyDataPipeline`: Coordinates `CSVDataLoader` -> `DataValidator` -> `FeatureEngine` -> `NautilusDataConverter` into a single unified preparation interface.
  2. **Feature Engineering**:
     - `FeatureEngine`: Computes 6 technical factors: Momentum (`rsi_14`, `roc_20`), Trend (`macd_hist`, `close_to_ma50`, `close_to_ma200`), and Volatility (`atr_14`, Bollinger Bands).
  3. **Target Engineering & Dataset Building**:
     - `TargetEngine`: Calculates 10-trading-day forward close return (`target_10d`) with negative shift ($t+10$), strictly decoupling factor computation from target labeling.
     - `TrainingDatasetBuilder`: Merges OHLCV features and forward targets, cleanly dropping lookahead and trailing NaNs to construct tabular datasets.
  4. **Machine Learning Model Layer**:
     - `BaseModel`: Abstract model contract defining `train(X, y)` and `predict(X)`. Completely decoupled from feature names, target horizons, universe definitions, and execution engines.
     - `XGBoostRegressor`: Concrete model wrapper around `xgboost.XGBRegressor`.
     - **Walk-Forward Expanding-Window Training**: Retrains at each monthly rebalance date using all past observations up to the target realization cutoff ($t_{\text{cutoff}} \le t_{\text{decision}} - \text{target\_horizon}$ trading sessions) to strictly prevent future lookahead bias.
     - **Fresh Model Clones**: Clones an unfitted instance per training fold via `copy.deepcopy(self.model_prototype)` to eliminate weight carryover between folds.
  5. **Cross-Sectional Stock Selection**:
     - `BaseSelector`: Abstract selector contract receiving predictions (`Symbol`, `score`) and returning `List[str]` of selected symbols.
     - Implementations: `TopNSelector` (top $N$ ranked stocks, default $N=20$), `PercentileSelector` (cross-sectional percentile cutoff), `ThresholdSelector` (score threshold).
  6. **Portfolio Construction**:
     - `BasePortfolio`: Abstract portfolio constructor receiving `List[str]` and returning `{Symbol: weight}`.
     - Implementations: `EqualWeightPortfolio` ($1/N$ capital allocation across selected symbols).
  7. **Risk Management & Capital Deployment (Complete for Current Scope)**:
     - `BaseRiskManager`: Minimal abstract base class defining `calculate_deployment_budget(portfolio_equity)`.
     - `FixedPercentageRiskManager`: Concrete implementation calculating permitted deployment budget as `portfolio_equity * max_deployment_pct` ($0.0 \le \text{max\_deployment\_pct} \le 1.0$).
     - The strategy uses a configured risk manager when supplied and defaults to 100% deployment (`FixedPercentageRiskManager(1.0)`) otherwise.
     - `Rebalancer`: Sizes target holdings using live portfolio equity and the risk manager's deployment budget (`target_value = deployment_budget * weight`).
     - Validates target weights: rejects negative weights and total target weights exceeding 100% (allowing small floating-point tolerance $\le 1.0001$).
     - **Scope Boundary**: Capital-deployment management is implemented and complete for the current scope. Stop-losses, take-profits, drawdown-based trading halts, per-position loss limits, and other risk-management methods are not part of the immediate plan.
  8. **Execution Rebalancer & Lifecycle Mechanics**:
     - `BaseStrategy` / `BaseStrategyConfig`: Small, reusable Nautilus `Strategy` template providing bar subscriptions, session-date extraction, and core execution configuration (`bar_types`, `venue`, `currency`, `exchange_tz`).
     - `XGBoostRegressorStrategy`: Multi-instrument orchestrator on NautilusTrader.
     - **Sole Rebalance Trigger**: Month-end trading session $T$ close on `on_bar()` is the sole trigger once all expected instruments report.
     - **Order Execution Timing**: Orders submitted at $T$ close (15:30 IST); Nautilus matching engine fills market orders against opening quotes at $T+1$ open (09:15 IST).
     - `Rebalancer`: Execution bridge operating on Nautilus `Portfolio`, `Cache`, and `OrderFactory`. Resolves instruments, tracks authoritative latest closing prices (`_latest_prices`), rounds quantities to step/lot sizes via `instrument.make_qty()`, liquidates omitted holdings (target weight 0), and deterministically submits SELL orders before BUY orders.
  9. **Execution Friction & Cost Modeling**:
     - `IndianEquityDeliveryFeeModel`: Models realistic statutory NSE cash-delivery transaction charges (Brokerage, STT, NSE transaction charge, SEBI turnover fee, Stamp duty, GST) and Depository Participant (DP) charges charged at most once per security per trading day on delivery sells.
     - `SlippageFillModel`: Models adverse tick-precision execution price slippage via synthetic L1 MBP `OrderBook` generation for Nautilus fill simulation.
  10. **Performance Reporting & Tearsheets**:
     - `NautilusExporter`: Extracts native NautilusTrader backtest artifacts (`orders.csv`, `order_fills.csv`, `fills.csv`, `positions.csv`, `account.csv`, `stats.csv`, `returns_series.csv`) and generates `tearsheet.html` via `nautilus_trader.analysis.create_tearsheet()`.
     - Custom recorder infrastructure, telemetry accumulators, and metadata manifest plumbing have been removed from the strategy execution flow.

- **Environment & Dependencies**:
  - Python 3.12 managed via UV (`uv.lock`).
  - NautilusTrader 2.0.0rc5, XGBoost, pandas, numpy.
  - Centralized logging (`configure_logging`) active across all strategy modules.

- **Roadmap & Next Implementation Steps**:
  - **Completed**:
    1. **Risk management — Complete for current scope.** Basic capital-deployment budgeting is implemented. No further risk-management methods are planned at this stage.
  - **Upcoming (Strict Order)**:
    2. **Trade Management module — Next.** Inspect the existing implementation first and determine what functionality already exists versus what the intended module still needs. Do not duplicate existing functionality or modify established execution behavior without an explicit reason.
    3. **Tax implementation.** Implement the intended tax deductions and financial-year treatment after clarifying the applicable requirements. Do not invent tax rates, assumptions, or rules.
    4. **Survivorship-bias handling.** Address survivorship bias in the historical investment universe and research workflow. Inspect the current data architecture before deciding on the implementation.
    5. **OpenStatz tearsheet.** Integrate OpenStatz for performance reporting and reconcile its relevant metrics against the existing Nautilus tearsheet. Identify discrepancies and their causes rather than assuming the metrics will match automatically.
    6. **Dashboard.** Develop the dashboard after the preceding work, using the actual available strategy results and reporting outputs.
