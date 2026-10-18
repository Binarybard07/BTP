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

---

## Upcoming Experiments

- **EXP-004 (Roadmap Step 2: Trade Management)**: Trade Management Module Integration & Evaluation
  - **Objective**: Inspect existing trade/order execution and rebalancing capabilities in `Rebalancer` and `XGBoostRegressorStrategy`, evaluate trade lifecycle tracking, and test trade management extensions without modifying verified execution behavior.

- **EXP-005 (Roadmap Step 3: Tax Implementation)**: Statutory Tax Drag & Net Return Impact
  - **Objective**: Incorporate statutory Indian capital gains tax treatment (STCG and LTCG accounting across financial years) into post-backtest equity curves and analyze post-tax performance impact.

- **EXP-006 (Roadmap Step 4: Survivorship-Bias Handling)**: Dynamic Universe Membership & Survivorship Bias Quantification
  - **Objective**: Ingest historical constituent lists for NIFTY indices over time to eliminate survivorship bias from point-in-time universe selection and benchmark results against survivorship-biased baselines.

- **EXP-007 (Roadmap Step 5: OpenStatz Tearsheet)**: OpenStatz Performance Analytics Reconciliation
  - **Objective**: Generate OpenStatz performance tearsheets and systematically reconcile return, risk, factor, and drawdown metrics against native NautilusTrader `tearsheet.html` outputs to identify and explain discrepancies.

- **EXP-008 (Roadmap Step 6: Dashboard)**: Interactive Dashboard Verification & Reporting Ingestion
  - **Objective**: Connect the interactive web dashboard to strategy output directories, verifying accurate display of equity curves, trade logs, risk metrics, and holding distributions.
