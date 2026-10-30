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

## Entry 004: Modular Backtesting Architecture Completion, Tradability Gate, Survivorship Control, and Statutory Tax
- **Date**: 2026-10-25
- **Topic**: Full System Architecture Integration & Engine Reconciliation
- **Summary**: Successfully unified the backtesting pipeline into a three-stage modular architecture (`qbeast_backtest` and `qbeast_exec`):
  - **Stage 1 (Data & Tradability)**: Integrated point-in-time index constituent membership across 8 dynamic DDQ universes (`nifty50_ddq` to `totalmarket_ddq`) eliminating survivorship bias. Enforced `TradabilityGate` (Staircase v2.4.1) filtering out illiquid and circuit-hit instruments.
  - **Stage 2 (Modular Strategy)**: Implemented `StrategyBase` contract consuming `MarketData` and returning `StrategyOutput`. Implemented expanding walk-forward retraining with strict target realization cutoffs ($T_{\text{cutoff}} \le T_{\text{decision}} - \text{target\_horizon}$) to eliminate future lookahead.
  - **Stage 3 (Dual Execution & Costs)**: Reconciled high-speed internal ledger simulation with event-driven NautilusTrader 2.0.0rc5 engine (Rs 0.0000 maximum difference). Modeled statutory Indian delivery fees (STT, turnover, SEBI, stamp, GST, DP charges), adverse tick slippage, price bands, and Indian capital gains tax (STCG at 20% / LTCG at 12.5% across April-March financial years).
  - **Reporting**: Integrated interactive `dashboard.html` performance tearsheet, daily `equity.csv`, and comprehensive 18-metric `summary.json`.
- **Current Pipeline**: `Stage 1 (loader + tradability)` -> `Stage 2 (strategy + ML walk-forward)` -> `Stage 3 (internal ledger / Nautilus execution)` -> `summary.json / dashboard.html`.
- **Outcome**: The architectural foundation is complete, verified, and frozen.

## Entry 005: Experiment Lab Multi-Agent Suite & Transition to Strategy Improvisation for Indian Equities
- **Date**: 2026-10-30
- **Topic**: Autonomous Experimentation Suite & Indian Equity Alpha Factor Research
- **Summary**: Built and verified the autonomous `Experiment_Lab` multi-agent framework and transitioned the project completely to quantitative strategy research and factor improvisation for Indian equities (NSE).
  - **Experiment Lab Multi-Agent Architecture**:
    - `generator_agent.py`: Renders parameterized strategy scripts from Jinja2 templates.
    - `runner_agent.py`: Executes strategies in parallel via `ThreadPoolExecutor` and automatically purges intermediate scratch folders (`Backtest_Module/reports/experiments/<run_id>`) upon staging results into `Backtest_Results/processed/<run_id>/` to prevent disk exhaustion.
    - `result_agent.py`: Parses 18 real metrics from `summary.json`, computes the benchmark-relative composite score ($0.5 \Delta\text{CAGR} + 0.5 \Delta\text{MaxDD}$), and formats `metrics.xlsx`.
    - `google_sheets.py`: Mirrors `metrics.xlsx` into Google Sheets in real time with frozen headers and conditional highlighting for outperforming runs ($\text{Score} \ge 0.0$).
  - **HPC Cluster Scalability**: Containerized inside `container.sif` on `/scratch/data/p23et0001/jenish/BTP` with Slurm. Tuned XGBoost to `tree_method: "hist"` and `n_jobs: 2` to eliminate CPU contention across 1,000+ combination sweeps.
  - **Active Research Framework for Indian Markets**:
    1. *Alpha Factor Engineering*: Developing scale-independent signals: cross-sectional momentum (12-1M, ROC 10d-252d), trend distances (`Close / SMA50`, `Close / SMA200`), realized volatility (10d, 21d, 63d), normalized ATR, volume surge ratios, and cross-sectional rank/z-score transformations.
    2. *Model Exploration*: Testing alternative supervised learners (LightGBM, CatBoost, pairwise ranking objectives) on top of the established `BaseModel` contract.
    3. *Execution Horizon Optimization*: Sweeping rebalance frequencies (weekly, monthly, quarterly) against target horizons (10d, 21d, 63d) to determine optimal trade-offs against Indian delivery transaction costs.
    4. *Downside Risk Mitigation*: Evaluating volatility-scaled position sizing and dynamic capital deployment to minimize drawdown during market crash regimes.
- **Current Pipeline**: `Experiment_Lab (generator -> runner -> result -> google_sheets)` -> `Backtest_Module (Stage 1-2-3)` -> `metrics.xlsx / Google Sheets`.

