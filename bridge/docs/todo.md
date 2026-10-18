# Project Task Queue (TODO)

## 🔴 Immediate Roadmap (Next Implementation Steps)

The project roadmap strictly follows this sequential progression:

1. **Risk Management — Complete for current scope**
   - [x] Basic capital-deployment budgeting is implemented via `BaseRiskManager` and `FixedPercentageRiskManager`, integrated into `Rebalancer`. Validates non-negative weights and total target weights $\le 1.0001$. No further risk-management methods are planned at this stage.

2. **Trade Management Module — Next**
   - [ ] **TRD-001**: Inspect existing trade/order execution and rebalancing implementations in `Rebalancer` and `XGBoostRegressorStrategy` to determine what trade management functionality already exists versus what is required. Avoid duplicating existing functionality or altering established execution behavior without explicit reason.

3. **Tax Implementation**
   - [ ] **TAX-001**: Implement statutory tax deductions and financial-year accounting treatment (STCG/LTCG) after clarifying applicable regulatory requirements. Avoid unverified assumptions or invented tax rates.

4. **Survivorship-Bias Handling**
   - [ ] **DAT-001**: Address survivorship bias in the historical investment universe and data pipeline. Inspect current data architecture and historical index constituent continuity before designing universe filtering.

5. **OpenStatz Tearsheet**
   - [ ] **REP-001**: Integrate OpenStatz for performance reporting and reconcile relevant metrics against existing NautilusTrader native tearsheet outputs (`tearsheet.html`). Systematically identify and document any metric discrepancies.

6. **Dashboard**
   - [ ] **UI-001**: Develop user-facing interactive dashboard using finalized strategy results, trade histories, and native reporting artifacts.

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
- [x] **IMP-010**: Implement Indian equity delivery fee model (`IndianEquityDeliveryFeeModel`) and slippage fill simulation (`SlippageFillModel`).
- [x] **IMP-011**: Implement minimal base strategy template (`BaseStrategy`, `BaseStrategyConfig`) decoupling core execution configuration from strategy-specific logic.
- [x] **IMP-012**: Implement capital-deployment risk management (`BaseRiskManager`, `FixedPercentageRiskManager`) and integrated deployment budget scaling into `Rebalancer`.
- [x] **IMP-013**: Simplify `XGBoostRegressorStrategy` by removing custom recorder infrastructure, enforcing deterministic component configuration, and delegating reporting directly to `NautilusExporter`.
