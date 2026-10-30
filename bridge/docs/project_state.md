# Project State

- **Current Project Phase**: **Architecture & Infrastructure COMPLETE & FROZEN**. Transitioned fully to **Phase 2: Strategy Improvisation, Alpha Factor Engineering & Quantitative Research for Indian Markets (NSE)** using the unified backtesting and Experiment Lab framework.

---

## 1. System Architecture Overview (Completed & Operational)

The end-to-end quantitative platform is fully built, tested, and operational across both local workstations and high-performance computing (HPC) clusters:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   STAGE 1: DATA & TRADABILITY GATING                   │
│  - CSVDataLoader & DataValidator: Robust per-file validation           │
│  - Point-in-Time Universe Membership: 8 DDQ universes (survivorship-free)│
│  - TradabilityGate (Staircase v2.4.1): Circuit & illiquidity filtering │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ MarketData contract
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   STAGE 2: MODULAR STRATEGY ENGINE                     │
│  - FeatureEngine: 16 stationary alpha factors (scale-independent)       │
│  - TargetEngine: Forward return labeling with lookahead realization cutoff│
│  - Machine Learning Model: BaseModel abstraction (XGBoost Hist)         │
│  - Cross-Sectional Selection: PercentileSelector, TopNSelector         │
│  - Portfolio Construction & Risk: FixedPercentageRiskManager           │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ StrategyOutput contract
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   STAGE 3: DUAL EXECUTION & REPORTING                  │
│  - Fast Internal Ledger & NautilusTrader 2.0.0rc5 Event Engine         │
│  - Indian Delivery Frictions: STT (0.1%), turnover, SEBI, GST, DP fees │
│  - Adverse Tick Slippage Model & Daily Circuit Price Bands             │
│  - Indian Capital Gains Taxation: STCG (20%) & LTCG (12.5%)            │
│  - Artifacts: summary.json (18 metrics), dashboard.html, equity.csv    │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │ summary.json & reports
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   EXPERIMENT LAB (MULTI-AGENT SUITE)                   │
│  - generator_agent: Jinja2 strategy script rendering from config.yaml  │
│  - runner_agent: Isolated parallel execution + intermediate cleanup    │
│  - result_agent: Benchmark-relative composite ranking & metrics.xlsx   │
│  - google_sheets: Real-time synchronization to Google Sheets           │
│  - Slurm & Singularity Cluster Integration on /scratch partition       │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Completed Architectural Modules

### 2.1 Stage 1: Data Ingestion, Survivorship Control & Tradability Gate
- **`CSVDataLoader` & `DataValidator`**: Robust ingestion of historical daily OHLCV data. Corrupted files are skipped with descriptive warnings without halting universe loading.
- **Survivorship-Bias Elimination**: Point-in-time constituent tracking across 8 dynamic DDQ universes (`nifty50_ddq`, `nifty100_ddq`, `nifty200_ddq`, `nifty500_ddq`, `midcap150_ddq`, `smallcap250_ddq`, `microcap250_ddq`, `totalmarket_ddq`).
- **`TradabilityGate` (Staircase v2.4.1)**: Pre-trade filter detecting illiquidity, price freezes, and upper/lower circuit limits on Indian equities. Stocks failing tradability criteria are excluded from the eligible set before model scoring.

### 2.2 Stage 2: Strategy Abstraction & Lookahead Prevention
- **Stationary Feature Matrix**: 16 certified technical factors across Momentum (`roc_10`, `roc_21`, `roc_63`, `roc_126`, `roc_252`, `return_30`), Moving Average ratios (`close_to_ma20`, `close_to_ma50`, `close_to_ma100`, `close_to_ma200`, `close_to_ema20`, `close_to_ema50`), Realized Volatility (`realized_vol_10`, `realized_vol_21`, `realized_vol_63`), and Oscillators (`rsi_14`, `macd_hist`, Bollinger Bands). All features are strictly scale-independent.
- **Walk-Forward Expanding Window**: Walk-forward retraining at each rebalance boundary using only data realized prior to the target realization cutoff ($T_{\text{cutoff}} \le T_{\text{decision}} - \text{target\_horizon}$), guaranteeing zero lookahead bias.
- **Selection & Sizing**: Pluggable selectors (`TopNSelector`, `PercentileSelector`) and portfolio constructors (`EqualWeightPortfolio`) scaled by `FixedPercentageRiskManager`.

### 2.3 Stage 3: Execution Engine & Realistic Indian Market Frictions
- **Dual Engine Capability**: Both high-speed internal ledger simulation and high-fidelity event-driven **NautilusTrader 2.0.0rc5** matching engine.
- **Indian Market Frictions**: `IndianEquityDeliveryFeeModel` modeling statutory STT (0.1% on delivery buys/sells), exchange turnover charges, SEBI fees, stamp duty, 18% GST, and daily Depository Participant (DP) charges.
- **Slippage & Circuit Bands**: `SlippageFillModel` simulating adverse tick-level market order slippage and daily exchange price bands.
- **Statutory Taxation**: Real-world Indian capital gains tax accounting distinguishing short-term (STCG) and long-term (LTCG) holding periods across Indian financial year boundaries (April 1 – March 31).
- **Reporting**: Native interactive tearsheet `dashboard.html`, daily marked-to-market `equity.csv`, executed `tradebook_backtest.csv`, and comprehensive 18-metric `summary.json`.

### 2.4 Experiment Lab: Autonomous Multi-Agent Experimentation
- **Autonomous Multi-Agent Architecture**:
  1. `generator_agent.py`: Expands parameter combinations from `config.yaml` using Jinja2 templates into self-contained executable strategy scripts.
  2. `runner_agent.py`: Executes strategies in parallel via `ThreadPoolExecutor` and cleans up intermediate scratch reports immediately upon completion.
  3. `result_agent.py`: Parses 18 verified metrics from `summary.json`, computes the benchmark-relative composite score, and compiles `metrics.xlsx`.
  4. `google_sheets.py`: Mirrors `metrics.xlsx` into Google Sheets with frozen top rows and soft-green highlighting for outperforming runs.
- **HPC Cluster Integration**: Tested and verified under Slurm and Singularity (`container.sif` + `.venv/bin/python`) on `/scratch/data/p23et0001/jenish/BTP` with optimized CPU thread scaling (`tree_method: "hist"`, `n_jobs: 2`).

---

## 3. Active Phase: Strategy Improvisation & Research Agenda

Now that the foundation is frozen, research focuses on developing superior quantitative trading strategies tailored specifically to the Indian stock market (NSE).

### 3.1 Research Objectives
1. **Alpha Factor Discovery**: Engineer and validate novel predictive signals for Indian equities (e.g., volume-weighted momentum, delivery percentage volume, 52-week high proximity, cross-sectional Z-score transforms).
2. **Predictive Modeling**: Explore alternative ML architectures including LightGBM, CatBoost, pairwise ranking objectives (`rank:pairwise`), and regularized linear models (Ridge/ElasticNet).
3. **Horizon & Frequency Optimization**: Systematically evaluate the trade-off between alpha signal freshness and statutory transaction costs across weekly, monthly, and quarterly rebalancing with 10-day, 21-day, and 63-day forward targets.
4. **Portfolio Weighting & Downside Protection**: Test volatility-scaled weighting, inverse-variance allocation, and downside-beta overlays to reduce max drawdown without sacrificing CAGR.
5. **Cross-Regime Robustness**: Verify strategy stability across major Indian market regimes:
   - Pre-COVID structural bull market (2016–2019)
   - COVID-19 crash and rapid rebound (2020)
   - Broad-market momentum rally (2020–2021)
   - Global rate tightening and sector rotation (2022–2024)
   - Current market all-time-high regime (2024–2026)

---

## 4. Environment & Dependencies

- **Python Version**: Python 3.12 managed via UV (`uv.lock`).
- **Core Packages**: NautilusTrader 2.0.0rc5, XGBoost, pandas, numpy, Jinja2, openpyxl, gspread.
- **HPC Cluster Setup**: Singularity container (`container.sif`), Slurm partition `standard`, workdir `/scratch/data/p23et0001/jenish/BTP`.
