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

### EXP-003: Full Event-Driven Nautilus Backtest with Friction & Risk Management
- **Status**: Completed
- **Date**: 2026-10-17
- **Strategy**: `XGBoostRegressorStrategy` on NautilusTrader 2.0.0rc5 `BacktestEngine`
- **Data Universe**: NIFTY 100 EoD dataset (processed via `StrategyDataPipeline`)
- **Friction Models**: `IndianEquityDeliveryFeeModel` (statutory STT, GST, turnover, stamp duty, DP sell charges) and `SlippageFillModel` (adverse tick slippage simulation)
- **Risk Management**: `FixedPercentageRiskManager` (100% allowable equity deployment budget) integrated into `Rebalancer` with target weight validation ($w_i \ge 0.0, \sum w_i \le 1.0001$)
- **Execution Mechanism**: Month-end session $T$ close sole rebalance trigger on `on_bar()`, market orders filled against $T+1$ opening quotes
- **Reporting**: Native Nautilus exports (`orders.csv`, `positions.csv`, `account.csv`, `stats.csv`, `returns_series.csv`) and HTML tearsheet via `NautilusExporter`
- **Summary**: Successfully executed full event-driven multi-instrument backtesting in NautilusTrader with realistic Indian equity market friction, live equity-based position sizing, capital-deployment budgeting, and native performance reporting.

### EXP-004: Tradability Gate (Staircase v2.4.1) & Trade Management Verification
- **Status**: Completed
- **Date**: 2026-10-22
- **Objective**: Integrate and verify the Staircase v2.4.1 tradability gate to filter out illiquid, staircase-pattern, and circuit-frozen stocks on NSE cash equities.
- **Summary**: Verified that `TradabilityGate` dynamically purges untradable stocks from the eligible set before model scoring, eliminating unexecutable trade orders and false paper profits from illiquid stocks.

### EXP-005: Statutory Indian Capital Gains Tax (STCG/LTCG) Integration
- **Status**: Completed
- **Date**: 2026-10-24
- **Objective**: Incorporate realistic statutory Indian capital gains tax treatment across financial years (April 1 to March 31).
- **Summary**: Integrated STCG (20%) and LTCG (12.5%) accounting into post-backtest equity calculations, producing realistic post-tax NAV curves in `equity.csv` and recording net tax liability in `summary.json`.

### EXP-006: Point-in-Time Membership & Survivorship Bias Elimination across 8 Universes
- **Status**: Completed
- **Date**: 2026-10-26
- **Objective**: Ingest historical constituent changes for NIFTY indices over 2016–2026 to eliminate survivorship bias.
- **Summary**: Configured and validated 8 survivorship-free DDQ universes (`nifty50_ddq`, `nifty100_ddq`, `nifty200_ddq`, `nifty500_ddq`, `midcap150_ddq`, `smallcap250_ddq`, `microcap250_ddq`, `totalmarket_ddq`). Backtests verified zero lookahead in universe inclusion.

### EXP-007: Dual-Engine Verification & Metric Standardization
- **Status**: Completed
- **Date**: 2026-10-28
- **Objective**: Verify that the fast internal ledger matches event-driven NautilusTrader 2.0.0rc5 execution and standardize output metrics.
- **Summary**: Acceptance test verified Rs 0.0000 maximum difference between both engines. Standardized `summary.json` to 18 verified metrics (CAGR, MaxDD, AvgDD, Sharpe, Calmar, benchmark metrics, charges, slippage, tax, fills).

### EXP-008: Interactive Performance Tearsheet Integration
- **Status**: Completed
- **Date**: 2026-10-28
- **Objective**: Generate standalone interactive HTML performance tearsheet and charts.
- **Summary**: Fully integrated `dashboard.html` generation, displaying cumulative return curves, benchmark comparisons, drawdown depth, rolling Sharpe, and monthly return heatmaps.

### EXP-009: Experiment Lab Autonomous Multi-Agent Suite & HPC Cluster Execution
- **Status**: Completed
- **Date**: 2026-10-30
- **Objective**: Build autonomous multi-agent experimentation framework (`generator_agent`, `runner_agent`, `result_agent`, `google_sheets`) and verify Slurm cluster execution.
- **Summary**:
  - Implemented parallel execution with automatic intermediate report directory cleanup (`Backtest_Module/reports/experiments/<run_id>`) preventing disk exhaustion.
  - Implemented benchmark-relative composite score: $\text{Score} = 0.5 \times (\text{CAGR} - \text{Benchmark\_CAGR}) + 0.5 \times (\text{MaxDD} - \text{Benchmark\_MaxDD})$.
  - Verified containerized execution (`container.sif` + `.venv/bin/python`) on `/scratch/data/p23et0001/jenish/BTP` with optimized XGBoost histogram tree method (`tree_method: "hist"`, `n_jobs: 2`).
  - Real-time Google Sheets synchronization with formatted headers and green conditional highlighting for outperforming runs.

---

## Active Research Experiments (Strategy Improvisation for Indian Markets)

### EXP-010: High-Throughput Grid Sweep (1,080 Combinations across 8 DDQ Universes)
- **Status**: Ready to Launch
- **Objective**: Run the comprehensive 1,080-combination grid sweep configured in `Experiment_Lab/config.yaml` across 8 DDQ universes, 3 rebalance frequencies (weekly, monthly, quarterly), 3 target horizons (10d, 21d, 63d), 5 selectors (percentile 80/85/90, top-n 15/20), and 3 risk ceilings (0.75, 0.85, 0.95).
- **Hypothesis**: Mid-cap and small-cap DDQ universes (`midcap150_ddq`, `smallcap250_ddq`) with monthly rebalancing and 21-day targets will yield the highest benchmark-relative alpha, while weekly rebalancing will suffer excessive STT fee drag.

### EXP-011: Alternative Tree Learners (LightGBM vs XGBoost Hist vs CatBoost)
- **Status**: Planned
- **Objective**: Benchmark LightGBM and CatBoost against XGBoost Hist on identical 16-feature matrices and walk-forward splits.
- **Hypothesis**: LightGBM will achieve comparable alpha with 30-40% faster training times, enabling faster walk-forward retraining across large equity universes.

### EXP-012: Advanced Indian Market Alpha Signals (Volume Dynamics & Proximity to Highs)
- **Status**: Planned
- **Objective**: Formulate and test new NSE-specific features: delivery volume surge, proximity to 52-week high, and cross-sectional z-score transformed momentum.
- **Hypothesis**: Volume-confirmed momentum will filter out false breakouts in Indian mid-caps, improving Calmar ratio and reducing maximum drawdown.

