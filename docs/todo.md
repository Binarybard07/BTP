# Project Task Queue (TODO)

## 🔴 Critical (Validation & Financial Correctness Audit)

- [ ] **VAL-001**: Verify `SignalDate` $T \to$ `EffectiveDate` $T+1$ maps strictly to actual consecutive trading sessions.
- [ ] **VAL-002**: Verify target weight $\to$ target portfolio value $\to$ target quantity $\to$ current quantity $\to$ order quantity pipeline math.
- [ ] **VAL-003**: Verify portfolio cash handling and unallocated cash accounting across rebalance events.
- [ ] **VAL-004**: Verify portfolio turnover calculation metric accuracy.
- [ ] **VAL-005**: Verify zero lookahead bias when ingesting real historical market data.
- [ ] **VAL-006**: Verify cross-sectional ranking is performed correctly across all active universe symbols per session.
- [ ] **VAL-007**: Verify removed holdings are actually reduced to zero target weight and exited in execution.
- [ ] **VAL-008**: Verify NautilusTrader receives and processes the intended target positions schedule without order rejection.
- [ ] **VAL-009**: Ensure sample-data performance results are explicitly tagged as software validation, **NOT** strategy profitability evidence.

---

## 🟡 Implementation

- [x] **IMP-001**: Set up local read-only HTTP context bridge server (`bridge/server.py`).
- [x] **IMP-002**: Implement bridge unit tests (`tests/test_bridge.py`).
- [x] **IMP-005**: Implement dedicated feature dataset persistence layer (`FeatureDatasetWriter` in `src/equity_strategy/features/writer.py`).
- [ ] **IMP-003**: Ingest real historical CSV market data into `data/raw/`.
- [ ] **IMP-004**: Implement configurable transaction cost model (brokerage, STT, exchange fees, slippage).

---

## 🔵 Research

- [ ] **RES-001**: Benchmark baseline strategy performance against Nifty 500 index returns.
- [ ] **RES-002**: Perform volatility regime sensitivity analysis (21-day rolling volatility filtering).
- [ ] **RES-003**: Evaluate parameter sensitivity across momentum lookbacks (e.g. 21, 63, 126, 252 days).

---

## 🟢 Later (Future Versions)

- [ ] **LAT-001**: XGBoost cross-sectional learning-to-rank model (`XGBoostRanker` implementing `SignalModel`).
- [ ] **LAT-002**: Portfolio optimization engine (GSA / Risk Parity / Mean-Variance implementing `PortfolioConstructor`).
- [ ] **LAT-003**: Ensemble ranking models combining baseline and ML scores.
