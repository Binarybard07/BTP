# Project State

- **Current Project Phase**: V1 implementation complete; financial correctness validation pending.
- **Current Architecture**: Decoupled 8-stage pipeline (`CSVDataLoader` -> `DataValidator` -> `UniverseProvider` -> `FeatureEngine` -> `BaselineSignal` -> `MonthlyRebalancer` -> `EqualWeightPortfolio` -> `TargetWeights` -> `NautilusTrader 2.0 Integration`).
- **Current Strategy**: Long-only cross-sectional momentum & trend equity strategy with monthly rebalancing.
- **Current Features**: `return_21`, `return_63`, `return_252`, `close_to_ma50`, `close_to_ma200`, `volatility_21`.
- **Current Portfolio Construction**: Top 50 candidates ranked by `FinalScore`, top 30 holdings selected with equal weighting ($w_i = 1/N$).
- **Current Nautilus Integration**: `TargetWeightStrategy` consuming target weights generated at $T$ close and executed at session $T+1$ via `BacktestEngine`.
- **Current Implementation Status**: Software-tested V1 system end-to-end (18 unit tests passing).
- **Current Known Issues**: Financial correctness validation pending (e.g. verifying exact cash handling, portfolio value tracking, turnover, and zero lookahead against real market data).
- **Current Research Question**: Does the cross-sectional momentum/trend baseline generate positive risk-adjusted excess returns over Nifty 500 benchmark on real historical Indian equity data?
- **Immediate Next Steps**: Perform thorough financial correctness validation of the V1 pipeline and prepare for real historical CSV data ingestion.
