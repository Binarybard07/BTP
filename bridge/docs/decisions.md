# Architectural & Research Decisions

## Decision 1: Separation of Alpha Generation and Portfolio Construction
- **Status**: Established
- **Rationale**: Alpha ranking models (`SignalModel`) generate relative strength scores per symbol, whereas portfolio constructors (`PortfolioConstructor`) convert scores into position target weights. Keeping them strictly decoupled ensures modularity when upgrading either component.

## Decision 2: Research Layer Independence from Execution Engine
- **Status**: Established
- **Rationale**: The research layer (`FeatureEngine`, `SignalModel`, `MonthlyRebalancer`, `PortfolioConstructor`) must not import or depend on `NautilusTrader`. NautilusTrader serves exclusively as the downstream execution and backtesting simulation engine.

## Decision 3: Long-Only Strategy & No Leverage
- **Status**: Established
- **Rationale**: V1 focuses on long-only Indian equity spot markets without short selling or leverage. Total target weights sum to $\le 1.0$.

## Decision 4: Monthly Rebalancing Cadence
- **Status**: Established
- **Rationale**: Rebalancing occurs on the last available trading date of each calendar month ($T$), with order execution scheduled for session $T+1$.

## Decision 5: Two-Stage Portfolio Selection (Candidate Pool + Equal Weighting)
- **Status**: Established
- **Rationale**: Cross-sectional ranking selects top 50 candidates, from which top 30 holdings are selected and assigned equal weight ($w_i = 1/N$). Exited stocks receive target weight $0.0$.

## Decision 6: Role of XGBoost in Future Releases
- **Status**: Established
- **Rationale**: When introduced, XGBoost will be used exclusively for cross-sectional ranking (learning to rank / relative returns), **not** for exact absolute price prediction.

## Decision 7: Exclusion of Fundamental & Factor Features in Current Design
- **Status**: Established
- **Rationale**: V1 research design strictly uses price and volume OHLCV technical indicators (`return_21`, `return_63`, `close_ma50`, `close_ma200`, `volatility_21`). No fundamental, balance sheet, or macroeconomic factors are included.

## Decision 8: Postponement of GSA (Genetic / Global Search Algorithms)
- **Status**: Established
- **Rationale**: GSA and advanced optimization techniques belong to future portfolio-construction iterations, not V1.

## Decision 9: Strict UV Package & Environment Management
- **Status**: Established
- **Rationale**: UV is the sole Python environment and dependency manager. All dependencies are pinned in `pyproject.toml` and locked in `uv.lock`. No `requirements.txt` or manual `pip` installs are allowed.
