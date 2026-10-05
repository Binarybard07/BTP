# Project Task Queue (TODO)

## 🔴 Immediate (Next Implementation Steps)

- [ ] **IMP-010**: Implement event-driven Nautilus `Strategy` subclass (e.g., `XGBoostNautilusStrategy`) integrating `MonthlyRebalanceStrategy` and `Rebalancer` into Nautilus `BacktestEngine`.
- [ ] **IMP-011**: Wire multi-symbol universe data loading, feature ingestion, and bar warmup in Nautilus backtest runner.

---

## 🟡 Research & Backtesting

- [ ] **RES-001**: Execute end-to-end backtest of XGBoost Regressor Strategy on full historical NIFTY 100 universe.
- [ ] **RES-002**: Implement transaction cost, slippage, and STT (Securities Transaction Tax) models in Nautilus backtest engine.
- [ ] **RES-003**: Generate performance tearsheets and risk analytics (Sharpe, Sortino, Max Drawdown, Calmar, monthly turnover).
- [ ] **RES-004**: Evaluate feature importance and performance across expanding vs. rolling training windows.

---

## 🟢 Completed Milestones

- [x] **IMP-001**: Implement Nautilus Data Converter & Strategy Data Pipeline (`NautilusDataConverter`, `StrategyDataPipeline`).
- [x] **IMP-002**: Implement first concrete Nautilus strategy (`MACrossoverStrategy`) running on Nautilus `BacktestEngine`.
- [x] **IMP-003**: Implement ML Target Engine (`TargetEngine` with `target_10d`) and `TrainingDatasetBuilder`.
- [x] **IMP-004**: Implement standardized ML model contract (`BaseModel`) and concrete `XGBoostRegressor`.
- [x] **IMP-005**: Implement cross-sectional selection (`BaseSelector`, `TopNSelector`) and portfolio construction (`BasePortfolio`, `EqualWeightPortfolio`).
- [x] **IMP-006**: Implement ML Strategy Orchestrator (`XGBoostRegressorStrategy`) with walk-forward expanding training windows and lossless result persistence (`StrategyResult`).
- [x] **IMP-007**: Implement strict per-CSV data validation (preserving missing values as NaNs, skipping corrupted files) and centralized logging (`configure_logging`).
- [x] **IMP-008**: Implement causal event-driven month-end rebalance scheduler (`BaseRebalanceStrategy`, `MonthlyRebalanceStrategy`).
- [x] **IMP-009**: Implement Nautilus execution rebalancer (`Rebalancer`) with live equity-based sizing and lot-size constraints.

