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

## Decision 17: Completion and Freezing of Foundational Infrastructure (Stage 1-2-3)
- **Status**: Established & Frozen
- **Rationale**: The core quantitative trading architecture is complete, verified, and locked:
  - Stage 1 (Data & Tradability): Point-in-time constituent membership across 8 dynamic DDQ universes eliminating survivorship bias, combined with the Staircase v2.4.1 tradability gate filtering circuit-locked and illiquid stocks.
  - Stage 2 (Modular Strategy): Strictly typed `MarketData` and `StrategyOutput` contracts, expanding walk-forward retraining with target realization cutoffs ($T_{\text{cutoff}} \le T_{\text{decision}} - H$), model/selector abstractions, and clean risk budgeting.
  - Stage 3 (Dual Execution & Costs): Dual execution engine (internal ledger simulation and event-driven NautilusTrader 2.0.0rc5 harness) with statutory Indian delivery fees (STT, turnover, SEBI, stamp, GST, DP fees), adverse tick slippage, price bands, and Indian capital gains tax (STCG/LTCG).
  - No further foundational infrastructure changes are permitted; all active work transitions to strategy research and factor improvisation.

## Decision 18: Completion and Operationalization of Experiment Lab Multi-Agent Suite
- **Status**: Established & Operational
- **Rationale**: The Experiment Lab is fully operational as an autonomous, multi-agent experimentation pipeline:
  - `generator_agent.py`: Renders parameterized strategy scripts from Jinja2 templates into `outbox/non_processed/`.
  - `runner_agent.py`: Concurrently executes strategy scripts via `ThreadPoolExecutor` and automatically purges intermediate report scratch folders (`Backtest_Module/reports/experiments/<run_id>`) upon staging results into `Backtest_Results/processed/<run_id>/` to prevent disk exhaustion.
  - `result_agent.py`: Extracts verified metrics from `summary.json`, computes the benchmark-relative composite score, and compiles formatted `metrics.xlsx`.
  - `google_sheets.py`: Mirrors `metrics.xlsx` into Google Sheets in real time with frozen headers and conditional highlighting.

## Decision 19: Apex Alpha Score Metric & Executive Matrix Sheet
- **Status**: Established
- **Rationale**: Strategy combinations in Experiment Lab are evaluated and ranked via the **Apex Alpha Score** ($\text{Score}_{\text{Apex}}$) against four strict Green criteria:
  1. $\text{CAGR} > 20.0\%$
  2. $\text{CAGR} > \text{Benchmark\_CAGR}$
  3. Drawdown depth $< 20.0\%$ (signed $\text{MaxDD} > -20.0\%$)
  4. Drawdown shallower than Benchmark (signed $\text{MaxDD} > \text{Benchmark\_MaxDD}$)
  
  - **Apex Alpha Scoring Formula**:
    $$\text{Score}_{\text{Apex}} = \begin{cases} 
    \left(\frac{\text{CAGR}}{|\text{MaxDD}|}\right) \times \left(1 + \frac{\text{CAGR} - \text{Benchmark\_CAGR}}{100}\right) & \text{if ALL 4 criteria pass} \\[10pt]
    \min\left(\text{CAGR} - \max(20.0, \text{Bench\_CAGR}), \;\; \text{MaxDD} - \max(-20.0, \text{Bench\_MaxDD})\right) & \text{if ANY criterion fails}
    \end{cases}$$
  - **Properties**:
    - $\text{Score}_{\text{Apex}} > 0 \iff \text{Apex Tier (Green)}$ (passes all 4 hurdles; minimum passing score is $> 1.0$).
    - When passing, directly maximizes CAGR while minimizing MaxDD via the Calmar Ratio.
    - When failing, $\text{Score}_{\text{Apex}} \le 0$, sorting models by the magnitude of their worst bottleneck deficit.
  - **Executive Matrix Sheet (`WORKSHEET_NAME_Matrix`)**:
    - Stored as a second tab in the same spreadsheet (`metrics.xlsx` and Google Sheets).
    - Groups results by `(Universe, Survivorship, From)`.
    - Selects the single best variation with $\text{Score}_{\text{Apex}} > 0$; displays `N/A` for strategy columns if no run meets the green hurdle.
    - Features a 2-tier header: `Universe`, `Survivorship`, `From`, `Run_ID`, `Score`, `Strategy` (CAGR, DD), `Index` (CAGR, DD).
    - Vertically merges the `From` column across `TRUE` and `FALSE` rows per universe, with solid outer frame borders, peach universe highlights, and rich color gradients on numbers.


## Decision 20: High-Throughput Cluster Execution (Singularity + Slurm + Hist XGBoost)
- **Status**: Established
- **Rationale**: Large grid sweeps (e.g., 1,000+ combinations) run on high-performance computing clusters under Slurm inside the containerized environment (`container.sif` + `.venv/bin/python`) on `/scratch/data/p23et0001/jenish/BTP`.
  - To prevent CPU thread contention and process stalls on multi-core Slurm compute nodes, XGBoost is configured to `tree_method: "hist"` and `n_jobs: 2` (rather than `n_jobs: -1`).
  - Slurm allocations use `--cpus-per-task=16` or `8` with multiple worker processes managed by `runner_agent.py`.

## Decision 21: Quantitative Research & Strategy Improvisation Framework for Indian Markets
- **Status**: Established
- **Rationale**: All future research leverages the frozen architecture to formulate robust alpha models specifically tuned to Indian equity market characteristics:
  - Only scale-independent, stationary features (ratios, percentage returns, normalized volatility, cross-sectional ranks) are permitted in cross-sectional models.
  - Lookahead bias is strictly prevented by expanding-window realization cutoffs.
  - Evaluations prioritize point-in-time `_ddq` universes across diverse market regimes (pre-COVID, 2020 crash, 2021 bull run, 2022-2024 rate cycle).

## Decision 22: Matrix Sheet Visual Architecture — Design 1C (Steel Graphite & Frost)
- **Status**: Implemented & Approved
- **Rationale**: Replaces saturated colors and green headers with an executive, non-green header palette to eliminate visual clutter and reserve green exclusively for positive alpha performance metrics:
  - **Headers (Cols A-E)**: Frosted Silver Gray (`#E5E7EB`) with dark graphite text (`#111827`).
  - **Strategy Super-Header (Cols F-G)**: Subtle Frosted Indigo (`#E0E7FF`) with bold deep indigo text (`#3730A3`); sub-headers in Pale Indigo (`#EEF2FF`).
  - **Index Super-Header (Cols H-I)**: Neutral Zinc (`#F4F4F5`) with deep zinc text (`#27272A`); sub-headers in Pale Zinc (`#FAFAFA`).
  - **Universe Column (Col A)**: Frost White / Soft Slate (`#F3F4F6`) with bold slate text (`#1F2937`).
  - **Metric Gradients**: Soft mint (`#D1FAE5`) and pastel green (`#C6F6D5`) for passing/superior performance; butter amber (`#FEF3C7`) and soft coral red (`#FEE2E2`) for sub-hurdle performance.
  - **Zero Green in Headers**: Ensures clear semantic separation between structural columns and positive return highlights.

