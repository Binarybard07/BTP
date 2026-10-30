# Project Task Queue (TODO)

> **Status: Architecture Phase COMPLETE & FROZEN**  
> **Active Phase: Strategy Improvisation, Alpha Factor Engineering & Quantitative Research for Indian Markets (NSE)**

---

## 🔴 Active Research Queue (Strategy Improvisation for Indian Equities)

The foundational architecture (Stage 1-2-3 and Experiment Lab) is frozen. All active work focuses on empirical factor research, strategy formulation, and machine learning optimization:

1. **Alpha Factor Engineering for Indian Equities (NSE)**
   - [ ] **RS-001**: Evaluate multi-horizon momentum factors (12-1 Month momentum, 10d/21d/63d/126d/252d ROC) across the 8 DDQ universes to quantify factor persistence and turnover drag under Indian delivery costs.
   - [ ] **RS-002**: Formulate volume-augmented signals (volume surge ratio vs 20-day SMA, delivery volume percentage, Amihud illiquidity metric) to identify institutional accumulation vs retail momentum.
   - [ ] **RS-003**: Implement and evaluate cross-sectional transformations (percentile ranking and z-score standardizations per decision date) to ensure cross-sectional comparability across high-beta and defensive NSE stocks.

2. **Machine Learning Model Exploration**
   - [ ] **RS-004**: Evaluate alternative tree-based algorithms (LightGBM and CatBoost) via the `BaseModel` abstraction, comparing out-of-sample Sharpe and Calmar against the baseline XGBoost Hist regressor.
   - [ ] **RS-005**: Test pairwise ranking loss objectives (e.g. `rank:pairwise` in XGBoost/LightGBM) to assess whether optimizing relative constituent rank outperforms predicting raw continuous forward returns.
   - [ ] **RS-006**: Benchmark regularized linear models (Ridge, ElasticNet) as interpretable baselines to measure non-linear interaction value in tree models.

3. **Rebalancing Frequency & Horizon Sensitivity**
   - [ ] **RS-007**: Run systematic parameter sweeps across rebalance frequencies (`weekly`, `monthly`, `quarterly`) and target horizons (`10`, `21`, `63` trading days) to determine the optimal trade-off between signal decay and statutory fee friction (STT, DP charges, slippage).

4. **Portfolio Sizing, Risk Overlays & Downside Protection**
   - [ ] **RS-008**: Test volatility-scaled allocation (inverse 21-day realized volatility weighting) against equal-weight portfolio construction to reduce portfolio drawdowns during broad market pullbacks.
   - [ ] **RS-009**: Evaluate dynamic capital deployment scaling based on market regime indicators (e.g. NIFTY 50 distance to 200-day SMA) to automatically preserve cash during bear regimes.

5. **Cluster Experimentation & High-Throughput Sweeps**
   - [ ] **RS-010**: Execute the 1,080-combination grid sweep via `Experiment_Lab` on the HPC Slurm cluster across all 8 DDQ universes.
   - [ ] **RS-011**: Analyze aggregated results in Google Sheets, filtering by benchmark-relative composite score ($\text{Score} \ge 0.0$), and isolate robust parameter sets across pre-COVID (2016-2019), COVID crash (2020), bull market (2020-2021), and rate-hike (2022-2024) periods.

---

## 🟢 Completed Architecture & Infrastructure Milestones

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
- [x] **TRD-001**: Implement Stage 1 Tradability Gate (`TradabilityGate` / Staircase v2.4.1) filtering illiquid and circuit-locked securities.
- [x] **TAX-001**: Implement statutory Indian capital gains tax module with STCG (20%) and LTCG (12.5%) accounting across Indian financial years (April 1 – March 31).
- [x] **DAT-001**: Integrate point-in-time constituent membership across 8 dynamic DDQ universes (`nifty50_ddq` to `totalmarket_ddq`) completely eliminating survivorship bias.
- [x] **REP-001**: Reconcile dual-engine execution (internal ledger vs NautilusTrader 2.0.0rc5 to Rs 0.0000 maximum difference) and standardize 18-metric `summary.json`.
- [x] **UI-001**: Integrate interactive tearsheet (`dashboard.html`), daily marked-to-market `equity.csv`, and executed `tradebook_backtest.csv`.
- [x] **LAB-001**: Build and verify the autonomous Experiment Lab multi-agent suite (`generator_agent`, `runner_agent`, `result_agent`, `google_sheets`) with Singularity/Slurm HPC cluster execution, intermediate folder cleanup, and real-time Google Sheets synchronization.
