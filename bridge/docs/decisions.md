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
