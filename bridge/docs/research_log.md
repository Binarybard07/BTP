# Research Log

## Entry 001: Pipeline Simplification & Baseline State
- **Date**: 2026-09-24
- **Topic**: Repository Cleanup & Base Pipeline Definition
- **Summary**: Outdated V1 research components (baseline cross-sectional signal ranker, equal-weight portfolio constructor, monthly rebalancer script) were removed.
- **Current Baseline Pipeline**: `CSVDataLoader` -> `DataValidator` -> `FeatureEngine` -> `Strategy`.
- **Next Research Step**: Evaluate EMA Crossover strategy mechanics in Nautilus-based framework.
