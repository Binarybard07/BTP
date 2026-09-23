# Experiment Registry

## EXP-001: Baseline Momentum & Trend Strategy on Synthetic Sample Data

- **Experiment ID**: EXP-001
- **Hypothesis**: The baseline multi-factor momentum and trend cross-sectional ranking strategy generates positive risk-adjusted returns when executed monthly on synthetic OHLCV data.
- **Features**: `return_21`, `return_63`, `close_ma50`, `close_ma200`
- **Target**: Relative 21-day and 63-day forward return ranking.
- **Training Period**: N/A (rule-based baseline score, 252-day lookback requirement).
- **Validation/Backtest Period**: 2021-01-01 to 2024-01-25 (800 trading sessions, 27 monthly rebalance sessions).
- **Portfolio Construction**: Candidate pool = 50, Holdings = 30, Equal Weight ($w_i = 1/30$). Long-only. No leverage.
- **Transaction-Cost Assumptions**: Brokerage = 0.0, Slippage = 0.0, Taxes = 0.0 (V1 baseline).
- **Metrics**:
  - Total Return: `23.71%`
  - Annualized Return: `6.93%`
  - Annualized Volatility: `4.38%`
  - Sharpe Ratio: `1.58`
  - Max Drawdown: `5.93%`
  - Total Orders: `1,138`
  - Total Positions: `98`
- **Result**: Execution completed successfully end-to-end without runtime errors or memory leaks.
- **Interpretation**: Software pipeline functionality is fully verified. Financial performance metrics on synthetic data serve purely as a baseline software check and carry no financial validity.
- **Status**: Completed (Software Validation Only)
- **Next Action**: Execute EXP-002 on historical raw Indian equity data once ingested.
