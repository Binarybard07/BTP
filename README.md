# Indian Equity Quantitative Trading Research System

Quantitative trading research system built with Python 3.12, UV, Polars/Pandas, Pytest, and NautilusTrader 2.0.0rc5.

---

## 📌 Architecture & Current Pipeline

The system pipeline consists of the following modular flow:

```
Data Loading (CSVDataLoader)
    ↓
Data Validation (DataValidator)
    ↓
Feature Engine (FeatureEngine & FeatureDatasetWriter)
    ↓
Strategy (BaseStrategy & EMACrossoverStrategy)
```

---

## 🛠️ Environment & UV Package Management

This project uses **UV** as the sole Python environment and dependency manager.

- **Python Version**: Pinned to `3.12` in `.python-version`.
- **NautilusTrader Version**: `2.0.0rc5` configured in `pyproject.toml`.
- **Dependencies**: Locked in `uv.lock`.

### Quickstart

1. **Synchronize Environment**:
   ```bash
   uv sync
   ```

2. **Run Unit Tests**:
   ```bash
   uv run pytest -v
   ```

---

## 📂 Current Codebase Structure

- `src/equity_strategy/data/`: `CSVDataLoader` and `DataValidator` for raw OHLCV ingestion.
- `src/equity_strategy/features/`: `FeatureEngine` and feature generators (`momentum.py`, `trend.py`, `volatility.py`, `writer.py`).
- `src/equity_strategy/strategy.py`: `BaseStrategy` interface and `EMACrossoverStrategy`.
- `bridge/`: Read-only HTTP documentation bridge server (`server.py`) and context docs.
- `tests/`: Pytest suite covering data loading, feature calculation, writer, and bridge server.
