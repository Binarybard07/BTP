# Architectural & Research Decisions

## Decision 1: Modular Strategy Pipeline Architecture
- **Status**: Established
- **Rationale**: Pipeline is decoupled into Data Loading -> Data Validation -> Feature Engine -> Strategy layers for clear separation of concerns and ease of testing.

## Decision 2: Feature Persistence Layer
- **Status**: Established
- **Rationale**: Calculated features are persisted into `{old_folder}_features/` directory via `FeatureDatasetWriter` to decouple feature computation from strategy consumption.

## Decision 3: Chronological Execution & Lookahead Prevention
- **Status**: Established
- **Rationale**: Strategy decision logic processes market data chronologically (bar-by-bar per symbol) ensuring decisions at bar T use only information available up to bar T.

## Decision 4: Strict UV Package & Environment Management
- **Status**: Established
- **Rationale**: UV is the sole Python environment and dependency manager. All dependencies are locked in `uv.lock`. No `requirements.txt` or manual `pip` installs are allowed.
