# Architectural & Research Decisions

## Decision 1: Modular Strategy Pipeline Architecture
- **Status**: Established
- **Rationale**: Pipeline is decoupled into Data Loading -> Data Validation -> Feature Engine -> Strategy layers for clear separation of concerns and ease of testing.

## Decision 2: Feature Persistence Layer
- **Status**: Established
- **Rationale**: Calculated features are persisted into `{old_folder}_features/` directory via `FeatureDatasetWriter` to decouple feature computation from strategy consumption.

## Decision 3: Chronological Execution & Lookahead Prevention
- **Status**: Established
- **Rationale**: Strategy decision logic processes market data chronologically (bar-by-bar per symbol) ensuring decisions at bar T use only information available up to bar T. Forward targets must incorporate realization cutoffs to prevent future leakage.

## Decision 4: Strict UV Package & Environment Management
- **Status**: Established
- **Rationale**: UV is the sole Python environment and dependency manager. All dependencies are locked in `uv.lock`. No `requirements.txt` or manual `pip` installs are allowed.

## Decision 5: Standardized Machine Learning Model Contract
- **Status**: Established
- **Rationale**: `BaseModel` defines only `train(X, y)` and `predict(X)`. The model layer remains completely decoupled from feature names, target horizons, universes, ranking, portfolio construction, rebalancing, and execution platforms. Concrete `XGBoostRegressor` implements this contract.

## Decision 6: Model-Agnostic Stock Selection & Portfolio Construction Abstractions
- **Status**: Established
- **Rationale**: Selection operates purely on a standardized DataFrame (`Symbol`, `score`) via `BaseSelector` (`TopNSelector`, `ThresholdSelector`, `PercentileSelector`). Portfolio construction operates purely on a `List[str]` of selected symbols via `BasePortfolio` (`EqualWeightPortfolio`), returning `{Symbol: weight}`. Neither component is coupled to ML models, risk optimization, or Nautilus.

## Decision 7: Causal Event-Driven Month-End Rebalance Scheduling
- **Status**: Established
- **Rationale**: Month-end rebalancing is detected causally without scanning the full historical calendar in advance. When the stream advances from session T in month M to session T+1 in month M+1, session T is causally identified as the completed month-end decision date. Information through T close is used for model inference and target portfolio construction. The scheduler operates at the portfolio level, absorbing intra-session multi-instrument bars to prevent redundant triggers.

## Decision 8: Nautilus Execution Rebalancer & Deterministic Sizing
- **Status**: Established
- **Rationale**: The execution-side `Rebalancer` reads authoritative portfolio equity from Nautilus (not initial capital or a parallel ledger) to dynamically size target positions (`target_value = current_equity * target_weight`). It enforces Nautilus instrument precision and lot-size constraints via `instrument.make_qty()`. It handles entering, adjusting, and exiting positions (missing instruments treated as target_weight=0). Sells are deterministically submitted before buys to free capital.

## Decision 9: Centralized Logging and Strict Non-Bypassable Validation
- **Status**: Established
- **Rationale**: Direct `print()` statements across `src/` are replaced by standard library `logging`. Data validation is strictly enforced per CSV file: invalid files are logged as warnings and skipped, while valid files are processed. No `validate=False` escape hatches are permitted.

## Decision 10: Lossless Result Persistence & Human-Readable Reporting
- **Status**: Established
- **Rationale**: Strategy runs encapsulate outputs in `StrategyResult`, providing `.to_text()` for a structured summary (rebalances, predictions, selected stocks, target weights) and `.save(output_dir)` saving both `strategy_result.pkl` (lossless pickle) and `strategy_result.txt`.

## Decision 11: Capital-Deployment Risk Management Architecture
- **Status**: Established
- **Rationale**: Capital deployment budgeting is encapsulated in `BaseRiskManager` and concrete `FixedPercentageRiskManager` (defaulting to 100% deployment if unspecified). The risk manager calculates the allowable deployment budget from live portfolio equity (`deployment_budget = risk_manager.calculate_deployment_budget(current_equity)`), which is passed directly into `Rebalancer.rebalance()`. Target positions are sized against this budget (`target_value = deployment_budget * weight`). `Rebalancer` validates that all target weights are non-negative and total target weight does not exceed 100% (allowing floating-point tolerance $\le 1.0001$). Stop-losses, take-profits, drawdown halts, and per-position loss limits are explicitly outside current scope.

## Decision 12: Minimal Base Strategy Template (`BaseStrategy`, `BaseStrategyConfig`)
- **Status**: Established
- **Rationale**: Shared Nautilus strategy boilerplate is isolated in `BaseStrategy` and `BaseStrategyConfig`. It handles bar subscriptions, session-date extraction, and core execution configuration (`bar_types`, `venue`, `currency`, `exchange_tz`). It deliberately excludes strategy-specific parameters, runtime datasets, model/selector configurations, and arbitrary kwargs dynamic assignment, maintaining strict modularity and clean compatibility with NautilusTrader 2.0.0rc5.

## Decision 13: Deterministic Strategy Configuration & Explicit Fallback Replacement
- **Status**: Established
- **Rationale**: Components (`model`, `selector`, `portfolio`, `risk_manager`, `fee_model`, `fill_model`, `feature_columns`) use explicit `is None` checks instead of truthiness/`or` fallbacks. If an explicit component or configuration value is passed, it is respected exactly; if omitted (`None`), explicit defaults are instantiated. Arbitrary `kwargs` introspection and dynamic `setattr` configuration patterns are prohibited. Runtime datasets are passed as explicit arguments rather than stored in the configuration object.

## Decision 14: Month-End Trading Session $T$ Close Sole Rebalance Trigger
- **Status**: Established
- **Rationale**: Rebalancing is triggered exclusively on month-end trading session $T$ close during `on_bar()` when all subscribed instruments have reported. Decisions use information available through $T$ close (15:30 IST) to train the model, select constituents, construct target weights, and submit market orders. Orders execute in the Nautilus simulation against opening quotes at $T+1$ open (09:15 IST). Lookahead bias is prevented by omitting any bars after $T$ close from the decision and realization cutoff ($t_{\text{cutoff}} \le t_T - 10$ sessions).

## Decision 15: Indian Equity Delivery Fee & Slippage Friction Modeling
- **Status**: Established
- **Rationale**: Real-world market frictions in the Indian cash equity segment are simulated via `IndianEquityDeliveryFeeModel` (covering STT, exchange turnover fees, SEBI charges, stamp duty, GST, and daily DP charges on delivery sales) and `SlippageFillModel` (adverse tick-level slippage simulated through synthetic L1 MBP book generation). This ensures backtest results realistically reflect transaction drag.

## Decision 16: Strategy Reporting Simplification & Recorder Decoupling
- **Status**: Established
- **Rationale**: Custom recorder infrastructure, telemetry accumulators, internal state tracking dictionaries/lists, and manifest export plumbing have been decoupled from the strategy execution loop. Strategy performance reporting delegates directly to native NautilusTrader output artifacts (`orders.csv`, `positions.csv`, `account.csv`, `stats.csv`, `returns_series.csv`) and HTML tearsheets via `NautilusExporter`.
