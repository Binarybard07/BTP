# Contract Specification

Data formats for input market data, strategy inputs/outputs, and generated backtest reports.

---

## 1. Input Files

### 1.1 EOD Stock Data (`<SYMBOL>.csv`)
Per-symbol historical end-of-day price and volume data. The strategy primarily consumes `date`, `close`, and `volume`.

- **Location**: `data/Final_Without_Survivorship_Data/<UNIVERSE_FOLDER>/<SYMBOL>.csv`
- **Header**:
```csv
date,open,high,low,close,adj_close,volume,open_interest,source,exchange,product_class,symbol,_source,_exchange,_window,_window_freq,_window_start,_window_end,_dq_score,_gap_filled,_merged_at
```
- **Sample Data (5 lines from `RELIANCE.csv`)**:
```csv
03-01-2000,21.8,23.1,21.8,23.1,23.1,48573772.0,0.0,upstox,NSE,EQUITY,RELIANCE,upstox,NSE,Jan-2000,monthly,01-01-2000,31-01-2000,0.9952,0,2026-08-29T22:38:15.547794
04-01-2000,23.7,24.95,23.05,24.95,24.95,103415208.0,0.0,upstox,NSE,EQUITY,RELIANCE,upstox,NSE,Jan-2000,monthly,01-01-2000,31-01-2000,0.9952,0,2026-08-29T22:38:15.547794
05-01-2000,23.55,26.4,23.55,25.9,25.9,147001184.0,0.0,upstox,NSE,EQUITY,RELIANCE,upstox,NSE,Jan-2000,monthly,01-01-2000,31-01-2000,0.9952,0,2026-08-29T22:38:15.547794
06-01-2000,26.5,27.6,26.5,27.0,27.0,147001184.0,0.0,upstox,NSE,EQUITY,RELIANCE,upstox,NSE,Jan-2000,monthly,01-01-2000,31-01-2000,0.9952,0,2026-08-29T22:38:15.547794
07-01-2000,27.1,29.2,26.9,28.85,28.85,147001184.0,0.0,upstox,NSE,EQUITY,RELIANCE,upstox,NSE,Jan-2000,monthly,01-01-2000,31-01-2000,0.9952,0,2026-08-29T22:38:15.547794
```

---

### 1.2 Benchmark Index Data (`<INDEX_NAME>.csv`)
Historical daily index prices used as the performance benchmark and trading calendar clock.

- **Location**: `data/Final_Without_Survivorship_Data/<UNIVERSE_FOLDER>/NIFTY 100.csv`
- **Header**:
```csv
date,open,high,low,close,adjusted_close,volume,open_interest
```
- **Sample Data (5 lines from `NIFTY 100.csv`)**:
```csv
02-01-2003,1008.0,1008.0,1008.0,1008.0,1008.0,0,0
03-01-2003,1004.4,1004.4,1004.4,1004.4,1004.4,0,0
06-01-2003,999.85,999.85,999.85,999.85,999.85,0,0
07-01-2003,997.81,997.81,997.81,997.81,997.81,0,0
08-01-2003,1005.3,1005.3,1005.3,1005.3,1005.3,0,0
```

---

### 1.3 Universe Membership Data (`<Index>_membership.csv`)
Point-in-time index constituent changes used when survivorship-bias control is enabled.

- **Location**: `data/Survivorship_Bias/Survivorship_Bias/Nifty100_membership.csv`
- **Header**:
```csv
effective_date,index_type,inclusions,exclusions,symbols,symbol_count
```
- **Sample Data (5 lines from `Nifty100_membership.csv`)**:
```csv
2026-09-14,Nifty 100,,,"ABB,ADANIENSOL,ADANIENT,ADANIGREEN,ADANIPORTS,ADANIPOWER,AMBUJACEM,APOLLOHOSP,ASIANPAINT,AXISBANK,...",100
2026-03-30,Nifty 100,"CUMMINSIND,HDFCAMC,MUTHOOTFIN,TATACAP,TMCV,UNIONBANK","BAJAJHFL,HAVELLS,ICICIGI,NAUKRI,JSWENERGY,LICI","ABB,ADANIENSOL,ADANIENT,ADANIGREEN,ADANIPORTS,ADANIPOWER,AMBUJACEM,APOLLOHOSP,ASIANPAINT,AXISBANK,...",100
2025-09-30,Nifty 100,"HINDZINC,MAXHEALTH,MAZDOCK,ENRIN,SOLARINDS","DABUR,HEROMOTOCO,ICICIPRULI,INDUSINDBK,SWIGGY","ABB,ADANIENSOL,ADANIENT,ADANIGREEN,ADANIPORTS,ADANIPOWER,AMBUJACEM,APOLLOHOSP,ASIANPAINT,AXISBANK,...",100
2024-03-28,Nifty 100,"ADANIPOWER,IRFC,JIOFIN,PFC,RECLTD","AWL,MUTHOOTFIN,PIIND,PGHH,UPL","ABB,ADANIENSOL,ADANIENT,ADANIGREEN,ADANIPORTS,ADANIPOWER,AMBUJACEM,APOLLOHOSP,ASIANPAINT,AXISBANK,...",100
2023-07-13,Nifty 100,JINDALSTEL,HDFC,"ABB,ADANIENSOL,ADANIENT,ADANIGREEN,ADANIPORTS,AMBUJACEM,APOLLOHOSP,ASIANPAINT,AWL,AXISBANK,...",100
```

---

## 2. In-Memory Strategy Contract

The strategy receives `MarketData` and outputs `StrategyOutput`.

- **Input (`MarketData`)**:
  - `history_closes`: Wide `pd.DataFrame` (`Date` index $\times$ `Symbol` columns).
  - `history_volumes`: Wide `pd.DataFrame` (`Date` index $\times$ `Symbol` columns).
  - `calendar`: Sequence of valid trading days (`pd.Timestamp`).
  - `eligible`: Mapping of `{decision_date: set(symbols)}` representing point-in-time tradable names.
- **Output (`StrategyOutput`)**:
  - `targets`: Dict mapping each rebalance session `Timestamp` to a dict of `{symbol: target_weight}`.
    - Example: `{Timestamp('2023-01-02'): {'SOLARINDS': 0.0535, 'IRFC': 0.0535, ...}}`
    - Constraint: Sum of weights $\le \text{max\_deployment\_pct} \le 0.97$.
  - `exposure`: `pd.Series` of total gross weight per decision date.
  - `posture`: `pd.Series` (`"sized"` or `"cash"`).
  - `name`: Strategy name (`"xgboost_regressor"`).
  - `params`: Dict of configured strategy parameters.

---

## 3. Generated Output Files

When executed through the runner, files are written to `Backtest_Module/reports/<UNIVERSE>/`.

### 3.1 Daily Curve (`equity.csv`)
Daily marked-to-market portfolio NAV, cash balance, deployment %, drawdown, and benchmark.

- **Location**: `Backtest_Module/reports/nifty100/equity.csv`
- **Header**:
```csv
date,nav,nav_pretax,invested,cash,deployment_pct,drawdown_pct,benchmark
```
- **Sample Data (5 lines)**:
```csv
2023-01-02,1000178.5714285715,1000178.5714285715,0.0,1000178.5714285715,0.0,0.0,1000000.0
2023-01-03,1005677.3216265328,1005677.3216265328,754773.94,250903.38162653288,75.0513036109103,0.0,1002301.6968196223
2023-01-04,992616.9558018233,992616.9558018233,741668.77,250948.18580182327,74.71852718865652,-1.2986636512381855,991226.8262218902
2023-01-05,995450.5479778593,995450.5479778593,744457.5499999999,250992.99797785934,74.78599027468294,-1.0169040733794432,989947.8027522186
2023-01-06,990165.5281560696,990165.5281560696,739127.71,251037.8181560696,74.64688367574627,-1.5424225183257811,982527.2849249768
```

---

### 3.2 Executed Trades (`tradebook_backtest.csv`)
Individual executed order fills with statutory transaction charges, slippage, and realized P&L.

- **Location**: `Backtest_Module/reports/nifty100/tradebook_backtest.csv`
- **Header**:
```csv
date,symbol,qty,price,cost,charges,slippage,realized_short,realized_long,notional
```
- **Sample Data (5 lines)**:
```csv
2023-01-03,SOLARINDS,12,4456.35,328.9670144021978,63.43485882120001,265.53215558099777,0.0,0.0,53476.200000000004
2023-01-03,IRFC,1623,33.0,109.56819243390667,63.533078334,46.03511409990667,0.0,0.0,53559.0
2023-01-03,PNB,932,57.45,231.46412353403772,63.51457320840001,167.94955032563772,0.0,0.0,53543.4
2023-01-03,UNIONBANK,655,81.7,146.89712715516333,63.479105051000005,83.41802210416331,0.0,0.0,53513.5
2023-01-03,PFC,443,120.7,79.15665526170764,63.4276228426,15.729032419107634,0.0,0.0,53470.1
```

---

### 3.3 Summary Metrics (`summary.json`)
Summary metrics including CAGR, Sharpe, max drawdown, frictions, and tax totals.

- **Location**: `Backtest_Module/reports/nifty100/summary.json`
- **Sample Excerpt**:
```json
{
  "universe": "nifty100",
  "cagr": 22.24,
  "ann_arith": 21.44,
  "maxdd": -19.37,
  "sharpe": 1.34,
  "benchmark_cagr": 15.62,
  "excess_cagr_pp": 6.62,
  "window_start": "2023-01-02",
  "window_end": "2024-12-31",
  "charges": 25878.42,
  "slippage": 43335.74,
  "tax": 82579.24,
  "fills": 501
}
```
