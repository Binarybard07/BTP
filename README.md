# Indian Equity Quantitative Trading Research & Backtesting System (V1)

V1 Indian Equity Quantitative Trading Research and Backtesting System built from scratch using Python 3.12, UV, Polars/Pandas, Pytest, and NautilusTrader 2.0.0rc5.

---

## 📌 Architecture & System Overview

The system strictly decouples the **Research Layer** from the **Nautilus Execution Layer**:

```
[Raw CSV Market Data (data/raw/ or data/sample/)]
                        │
                        ▼
       [CSVDataLoader & DataValidator]
                        │
                        ▼
                [UniverseProvider]
                        │
                        ▼
                 [FeatureEngine]
  (return_21, return_63, return_252, close_ma50, close_ma200, volatility_21)
                        │
                        ▼
                 [BaselineSignal]
   (Cross-sectional percentile ranking, MomentumScore, TrendScore, FinalScore)
                        │
                        ▼
               [MonthlyRebalancer]
  (SignalDate T [month end close] -> EffectiveDate T+1 [next session])
                        │
                        ▼
             [EqualWeightPortfolio]
  (Select top 50 candidates -> top 30 holdings equal-weighted)
                        │
                        ▼
           [Target Weights Schedule]
  (data/outputs/target_weights.parquet & target_weights.csv)
                        │
                        ▼
         [NautilusTrader 2.0 Integration]
  (ParquetDataCatalog -> BacktestEngine -> TargetWeightStrategy)
                        │
                        ▼
              [Performance Analysis]
  (performance_summary.csv, equity_curve.csv & equity_curve.png)
```

---

## 🛠️ Environment & UV Package Management

This project uses **UV** as the sole Python environment and dependency manager.

- **Python Version**: Pinned to `3.12` in `.python-version`.
- **NautilusTrader Version**: `2.0.0rc5` pre-release configured in `pyproject.toml`.
- **Dependencies**: Fully locked in `uv.lock`.

### Setup Instructions

1. **Synchronize Virtual Environment**:
   ```bash
   uv sync
   ```

2. **Verify Python Version**:
   ```bash
   uv run python --version
   ```

3. **Verify NautilusTrader Installation**:
   ```bash
   uv run python -c "import nautilus_trader; print(nautilus_trader.__version__)"
   ```

---

## 🚀 Quickstart: Running End-to-End

Run the complete pipeline from scratch using synthetic sample data:

```bash
# 1. Synchronize UV environment
uv sync

# 2. Run unit test suite
uv run pytest -v

# 3. Generate synthetic market data (~100 stocks, 3+ years)
uv run python scripts/generate_sample_data.py

# 4. Run research pipeline & generate target weights
uv run python run_research.py --data-dir data/sample

# 5. Run NautilusTrader 2.0 backtest
uv run python run_backtest.py --data-dir data/sample
```

---

## 📊 V1 Quantitative Strategy Details

### 1. Feature Engine (`FeatureEngine`)
Calculated strictly per symbol (zero cross-symbol data leakage):
- `return_21`: $Close[t] / Close[t-21] - 1$
- `return_63`: $Close[t] / Close[t-63] - 1$
- `return_252`: $Close[t] / Close[t-252] - 1$
- `close_ma50`: $Close[t] / \text{MA}_{50}[t]$
- `close_ma200`: $Close[t] / \text{MA}_{200}[t]$
- `volatility_21`: 21-day rolling standard deviation of daily returns

### 2. Signal Model (`BaselineSignal`)
Calculates cross-sectional percentile ranks on each date:
- $\text{MomentumScore} = 0.4 \times \text{rank}(\text{return\_21}) + 0.6 \times \text{rank}(\text{return\_63})$
- $\text{TrendScore} = 0.5 \times \text{rank}(\text{close\_ma50}) + 0.5 \times \text{rank}(\text{close\_ma200})$
- $\text{FinalScore} = 0.6 \times \text{MomentumScore} + 0.4 \times \text{TrendScore}$

### 3. Monthly Rebalancer & Portfolio Construction
- Rebalances on the **last trading session of each month** (`SignalDate` $T$).
- Filters top 50 candidates, selects top 30 holdings.
- Assigns equal weight $w_i = 1 / N$.
- Execution occurs on **next available trading session** (`EffectiveDate` $T+1$).

---

## 🛡️ Lookahead Bias Prevention

Lookahead bias is strictly prevented:
1. Features and signals at date $T$ use **only** market data available up to and including $T$.
2. Signals generated at session $T$ close are executed on session $T+1$ (never at session $T$ close).
3. Unit test `tests/test_pipeline.py::test_lookahead_bias_prevention` explicitly verifies that injecting future price shocks after date $T$ leaves signal values at date $T$ unchanged.

---

## 📈 Output Files

All outputs are written to `data/outputs/`:
- `target_weights.parquet` & `target_weights.csv`: Target weight schedule with `SignalDate`, `EffectiveDate`, `Symbol`, `Rank`, `FinalScore`, `TargetWeight`.
- `performance_summary.csv`: Performance metrics (Total Return, Annualized Return, Volatility, Sharpe Ratio, Max Drawdown, Turnover).
- `equity_curve.csv`: Daily portfolio values.
- `equity_curve.png`: Rendered equity curve plot.

---

## 🔮 V2 Roadmap & Extensibility

The architecture enforces abstract base classes (`SignalModel`, `PortfolioConstructor`) for seamless extension:
- **ML Signal Models**: Integrate XGBoost, LightGBM, or Ensemble models by inheriting from `SignalModel`.
- **Optimization Portfolios**: Integrate Risk Parity or Mean-Variance Optimization by inheriting from `PortfolioConstructor`.
