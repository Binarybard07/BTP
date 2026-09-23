# Research Log

## Entry 001: Baseline Signal Formulation & Feature Weighting
- **Date**: 2026-09-22
- **Topic**: V1 Baseline Score Definition
- **Hypothesis**: Combining short-term (21-day) and medium-term (63-day) price momentum with short-term (50-day) and long-term (200-day) trend alignment provides robust cross-sectional stock ranking for monthly rebalancing.
- **Formulation**:
  - $\text{MomentumScore} = 0.4 \times \text{rank}(\text{return\_21}) + 0.6 \times \text{rank}(\text{return\_63})$
  - $\text{TrendScore} = 0.5 \times \text{rank}(\text{close\_to\_ma50}) + 0.5 \times \text{rank}(\text{close\_to\_ma200})$
  - $\text{FinalScore} = 0.6 \times \text{MomentumScore} + 0.4 \times \text{TrendScore}$
- **Methodology Decision**: Ranks are calculated cross-sectionally per date using percentile ranking ($\text{rank}(pct=\text{True})$). Stocks with missing feature values on a date are excluded from ranking for that session.
- **Conclusion**: Software implementation verified cleanly. Next phase will test baseline performance against real historical market data.

## Entry 002: Lookahead Prevention Protocol ($T \to T+1$)
- **Date**: 2026-09-22
- **Topic**: Execution Timing & Information Boundaries
- **Hypothesis**: Generating target weights at session $T$ close and executing orders on session $T+1$ prevents lookahead bias.
- **Methodology Decision**: `SignalDate` is set to session $T$ close. `EffectiveDate` is set to session $T+1$. Target weight orders execute at session $T+1$.
- **Conclusion**: Verified via unit test `tests/test_pipeline.py::test_lookahead_bias_prevention`. Shocking future prices after session $T$ leaves signal scores at session $T$ 100% unchanged.
