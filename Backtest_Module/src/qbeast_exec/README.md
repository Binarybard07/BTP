# qbeast_exec — shared execution-accounting library

**The problems every strategy has to solve, solved once, the same way for everyone.**

| Module | What it does | Needs Nautilus? |
|---|---|---|
| `qbeast_exec.tax` | Indian capital-gains tax — FIFO lots, date-dependent rates, FY assessment | No |
| `qbeast_exec.charges` | Itemised transaction charges **+** slippage (two-sided, size-aware) | Only for the 2 adapters |
| `qbeast_exec.survivorship` | **Point-in-time index membership — the survivorship-bias control** | No |
| `qbeast_exec.universe` | **Per-universe output folders + an honest cross-universe roll-up** | No |
| `qbeast_exec.bands` | Price bands / circuit locks, **staircase flag**, market-wide breakers | No |
| `qbeast_exec.books` | Any Nautilus engine → order book, trade book, per-stock roll-up | Yes |
| `qbeast_exec.ledger` | **Cash / position / FIFO-tax ledger + the Nautilus fill replay** — the NAV Nautilus cannot give you | No |
| `qbeast_exec.nautilus` | **Instruments, bars, and the T → T+1 execution contract** | Only for instruments/bars |
| `qbeast_exec.report` | Shared performance stats + a self-contained HTML dashboard | No |

Nothing imports any strategy's `config.py`. Every parameter arrives through a dataclass with sensible
defaults, so the package drops into any project unchanged.

```python
from qbeast_exec import (TaxEngine, TaxConfig,
                         ChargesConfig, ChargesCalculator, NautilusFeeModel,
                         SlippageConfig, SlippageModel, NautilusFillModel,
                         Membership, SurvivorshipConfig, assert_no_look_ahead,
                         UniverseReport,
                         PriceBandModel, BandConfig, StaircaseFlag, StaircaseConfig,
                         MarketWideBreaker, export_books,
                         Ledger, apply_fill, replay_fills, aftertax_nav,
                         make_equity, make_bars, DeferredSubmit,
                         Dashboard, performance_stats)
```

**Install:** copy the `qbeast_exec/` folder next to your code (or add its parent to `PYTHONPATH`).
Requires `pandas`, `numpy`; `plotly` optional (charts), `nautilus_trader` optional (adapters).

## Verify the drop-in before you build on it

```bash
python -m qbeast_exec.selftest          # 190 assertions on the accounting  -> "190 passed, 0 failed"
python -m qbeast_exec.isolation_audit   # 120 public callables, from YOUR project -> "SELF-CONTAINED"
```

The two check different things and you want both. `selftest` checks the **accounting** — every tax
rate/regime combination, FIFO splitting across the holding-period boundary, each charge component,
slippage two-sidedness, drawdown metrics. `isolation_audit` checks the **packaging**: that every public
callable runs from your copy alone, and that nothing reaches back into the project this came from.

That second check is the one that matters when a folder changes hands. A library which quietly imports
someone's `config.py` passes every test in its origin repo and dies on the first machine it is copied
to. Nothing here does — verified by snapshotting `sys.modules` before the import and asserting no
project module appears after exercising all 120 callables.

**`API_SIGNATURES.md`** is the exact signature of every export, generated from the source
(`python -m qbeast_exec.gen_signatures`). Read its first section before you start: it lists the
**18 signatures that mislead**, each of which was actually got wrong while auditing *with the source
open* — `PriceBandModel.infer_band` takes a return series rather than a symbol,
`TaxEngine.apply_fills` keys on `date`/`price` rather than `ts`/`px`, `SlippageModel.realised()` takes
no arguments, and `Dashboard` is a dataclass whose `title` is a field, so `dash.title('x')` raises.

## Drop-in quickstart — order on T's close, fill at T+1's open

Everything below is in this package. Nothing else is needed.

```python
import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine
from qbeast_exec import (ChargesConfig, SlippageConfig, SlippageModel, TaxConfig,
                         NautilusFeeModel, NautilusFillModel,
                         make_equity, make_bars, DeferredSubmit,
                         replay_fills, export_books, performance_stats, Dashboard)

CHARGES, TAX = ChargesConfig.equity_delivery(), TaxConfig()
slip = SlippageModel({s: ohlcv[s] for s in symbols}, SlippageConfig())

# ---- 1. venue: statutory charges on every fill, slippage in the fill PRICE
engine.add_venue(..., fee_model=NautilusFeeModel(CHARGES),
                      fill_model=NautilusFillModel(slip))

# ---- 2. bars: the CLOCK instrument first (offset_ns=0), tradables one nanosecond later (=1).
#         This is what makes an order submitted on the clock bar fill at the day's OPEN.
clock = make_equity("NIFTY 50", venue="XNSE")
engine.add_instrument(clock)
engine.add_data(make_bars(clock, idx, idx, idx, idx, None, offset_ns=0))
for s in symbols:
    inst = make_equity(s, venue="XNSE")
    engine.add_instrument(inst)
    engine.add_data(make_bars(inst, opens[s], highs[s], lows[s], closes[s], vols[s], offset_ns=1))

# ---- 3. strategy: decide on t, submit on t+1
class MyStrategy(Strategy):
    def on_start(self):
        self.defer = DeferredSubmit()

    def on_bar(self, bar):
        if bar.bar_type.instrument_id != clock.id:
            return                                   # only the clock bar drives the schedule
        day = pd.Timestamp(bar.ts_event).date()
        for decided_on, target in self.defer.release():
            self.submit_target(target)               # the PRIOR day's decision -> fills at today's open
        if day in rebalance_dates:
            self.defer.queue(day, my_target_weights(day))   # {} means flatten to cash

engine.run()

# ---- 4. NAV: Nautilus models neither tax nor idle-cash yield, so apply them here.
#         opens= reprices each fill to the fill-day open (see below).
led = replay_fills(FILLS, closes, calendar, initial=1_000_000, opens=opens,
                   tax_config=TAX, charges_config=CHARGES, cash_yield=0.04)

# ---- 5. reporting
export_books(engine, "backtest", out_dir, order_dates=ORDER_DATES, opens=opens)
print(performance_stats(led.equity_curve()))
Dashboard(led.equity_curve(), benchmark=index_nav, title="My strategy").write(out_dir / "report.html")
```

### The three things that make T → T+1 correct

All three are required; any one alone leaves a hole.

| | Mechanism | Without it |
|---|---|---|
| **Day** | `DeferredSubmit` holds the decision and releases it on the next clock bar | an order submitted on `t` fills on `t` — the decision reads the price it then trades at |
| **Bar order** | `offset_ns=0` clock, `offset_ns=1` tradables | equal offsets make the fill price depend on feed order, i.e. undefined |
| **Price** | `replay_fills(opens=)`, `export_books(opens=)` | ~99.9% of fills carry the **previous session's close** — a price that was never executable, and in a momentum book systematically favourable |

The third is the dangerous one: the day is right, so a date audit passes, and the error hides in the
price. Measured on a real run before it was fixed: a mean **+0.25%** discount per fill.

> **`FILLS` and `ORDER_DATES`** are yours to collect — one `dict(ts=, symbol=, qty=, px=, comm=)` per
> `on_order_filled`, and `client_order_id -> decision date` at submission. `order_dates` is what lets
> the exported order book show **when the order was decided (t)** beside when it filled (t+1); without
> it you cannot audit the rule.

**Verify it agrees with your arithmetic** — 160 assertions, every one hand-computed:

```bash
python -m qbeast_exec.selftest
```

---

# 1. Tax module — `qbeast_exec.tax`

Implements listed-equity capital gains (s.111A short-term, s.112A long-term) with the **date-dependent**
rates from the Finance Act 2024. **The rate is set by the SALE date, not the purchase date.**

| | held < 365d (STCG) | held ≥ 365d (LTCG) |
|---|---|---|
| sale **before** 23-Jul-2024 | 15% | 10% above ₹1,00,000 |
| sale **on/after** 23-Jul-2024 | 20% | 12.5% above ₹1,25,000 |

### `TaxConfig` — all inputs

| Field | Type | Default | Meaning |
|---|---|---|---|
| `regime_change` | Timestamp | `2024-07-23` | date the new rates take effect |
| `stcg_rate_old` / `stcg_rate_new` | float | `0.15` / `0.20` | short-term rate before / after |
| `ltcg_rate_old` / `ltcg_rate_new` | float | `0.10` / `0.125` | long-term rate before / after |
| `ltcg_exemption_old` / `_new` | float | `100_000` / `125_000` | **annual** LTCG exemption |
| `exemption_change_fy` | int | `2024` | first FY using the new exemption |
| `ltcg_hold_months` | int | `12` | **statutory holding period, in calendar months** |
| `ltcg_hold_days` | int⎮None | `None` | set (e.g. `365`) to force a raw day-count instead |
| `ltcg_boundary_inclusive` | bool | `False` | `False` = strictly **more than** 12 months (the law) |
| `fy_start_month` | int | `4` | Indian FY starts 1 April |
| `carry_forward_losses` | bool | `False` | s.74 carry-forward (see below) |
| `carry_forward_years` | int | `8` | assessment years a loss survives |
| `cess` | float | `0.0` | e.g. `0.04` for 4% health & education cess |
| `surcharge` | float | `0.0` | applied **before** cess |

> **Defaults reproduce the base statutory computation.** Turn on `carry_forward_losses` and `cess` for a
> more realistic figure — see *Choosing settings* below.

### `TaxEngine` — API

| Method | Purpose |
|---|---|
| `buy(symbol, date, qty, price)` | record a purchase → creates a FIFO lot |
| `sell(symbol, date, qty, price)` | record a sale → returns `{"short": x, "long": y}` realised on it |
| `assess(date) -> float` | tax becoming payable now; fires **once per FY**, at/after 31-Mar. Call daily. |
| `assess_all() -> DataFrame` | assess every year regardless of date (for reporting) |
| `tax_for(fy) -> float` | what-if: tax for one FY without recording it |
| `statement() -> DataFrame` | one row per assessed FY (the FY tax statement) |
| `closed_lots() / open_lots()` | every realised slice / current holdings |
| `holding_period_summary()` | per-symbol median days held + STCG/LTCG split |
| `position(symbol)` | current quantity held |
| `apply_fills(fills, ...)` | bulk-load a DataFrame/list of fills (**signed** qty: +buy, −sell) |

```python
from qbeast_exec import TaxEngine, TaxConfig

tax = TaxEngine(TaxConfig())
tax.buy("RELIANCE", "2023-04-10", 100, 2400.0)
tax.sell("RELIANCE", "2024-09-01", 100, 2900.0)     # held 509 days → LTCG, new regime
print(tax.assess("2025-03-31"))                     # → 0.0
print(tax.statement())
```

> That `0.0` is **correct, not a bug**: the ₹50,000 long-term gain is below the ₹1,25,000 annual
> exemption. Buy on `2024-01-01` instead (holding 244 days, so short-term) and the same ₹50,000 gain is
> taxed at the new 20% rate = **₹10,000** — short-term has no exemption.

**Loading from an existing fill log** (any source — Nautilus export, broker contract notes, CSV):

```python
tax = TaxEngine()
tax.apply_fills(df, symbol="symbol", date="date", qty="qty", price="price")   # qty signed
stmt = tax.assess_all()
```

### Holding period — 12 **months**, not 365 days

s.2(42A) makes a listed equity share short-term when held **"not more than 12 months"**, so long-term
requires **more than 12 calendar months**. That is not the same as 365 days:

* a 12-month span containing **29 February is 366 days**, so a 365-day rule turns a lot long-term *early*;
* month lengths differ, so the day-count boundary drifts with the purchase date.

Both effects push gains into the **cheaper** LTCG bucket prematurely, i.e. a 365-day rule **understates
tax**. Calendar months are the default.

| buy | sell | days | 12-month rule | 365-day rule |
|---|---|---|---|---|
| 2023-03-01 | 2024-02-29 | 365 | **STCG** | LTCG ← wrong |
| 2023-03-01 | 2024-03-01 | 366 | **STCG** (exactly 12m) | LTCG ← wrong |
| 2023-03-01 | 2024-03-02 | 367 | LTCG | LTCG |

```python
cfg = TaxConfig()
cfg.long_term_on_or_after("2023-03-01")        # Timestamp('2024-03-02') — first LTCG-eligible sale date
cfg.is_long_term("2023-03-01", "2024-02-29")   # False
TaxConfig(ltcg_hold_days=365).is_long_term("2023-03-01", "2024-02-29")   # True (legacy behaviour)
```

`pd.DateOffset` handles month-ends and leap days: `29-Feb-2024 + 12m → 28-Feb-2025` (so long-term from
1-Mar-2025); `31-Jan-2023 + 12m → 31-Jan-2024` (long-term from 1-Feb-2024).

### Rules implemented (important details)

* **FIFO.** One sale can span several lots and be **part short-term, part long-term**.
* **Four buckets per FY** — {short, long} × {old, new regime}. This is what makes FY2024-25, which
  straddles the change, computable.
* **Within-year netting.** A loss in one bucket offsets a gain in the other *before* tax. A net-loss year
  pays nil.
* **LTCG exemption** is annual and applies to the **net**, consumed chronologically (old-regime gains
  first), so the residual is taxed at the new rate first.
* **Carry-forward (opt-in, s.74).** STCL offsets STCG then LTCG; LTCL offsets **only** LTCG; 8 years.
  With it **off** (the default) a loss is discarded, which **overstates** lifetime tax.
* **Assessment timing.** Once per FY, at/after 31-March, as a lump sum. Real advance tax is quarterly.

### Not modelled

* **s.112A grandfathering** (31-Jan-2018 fair-market-value step-up). Irrelevant from 2019; material for
  backtests starting before 2018.
* The portfolio is treated as a **standalone taxpayer** — the ₹1.25 lakh exemption is legally per *person*
  across all their holdings.

---

# 2. Charges module — `qbeast_exec.charges`

Two things, deliberately separate because they behave differently:

* **Charges** — deterministic statutory/broker costs. Always a **cost** (cash out), never a price change.
* **Slippage** — execution uncertainty. Can be a cost, or a genuine **price move** (two-sided).

## 2a. `ChargesConfig` / `ChargesCalculator`

Charges are **itemised**, not one blended constant, so you can see exactly what you pay and switch segment
without re-deriving anything.

| Field | Default (delivery) | Meaning |
|---|---|---|
| `brokerage_pct` | `0.0` | fraction of turnover (0 = discount broker) |
| `brokerage_flat_max` | `0.0` | per-order cap in currency (0 = no cap) |
| `stt_buy` / `stt_sell` | `0.001` / `0.001` | securities transaction tax — **the dominant term** |
| `exchange_txn` | `0.0000297` | NSE cash 0.00297% |
| `sebi_fee` | `0.000001` | ₹10 per crore |
| `stamp_duty_buy` / `_sell` | `0.00015` / `0.0` | 0.015%, **buy side only** |
| `gst_rate` | `0.18` | on (brokerage + exchange + SEBI) |
| `dp_charge_flat_sell` | `0.0` | **flat** currency amount per sell per scrip (e.g. `15.0`) |

**Presets:** `ChargesConfig.equity_delivery()`, `.equity_intraday()`, `.futures()`, `.options()`,
`.blended(rate)`. Any field is overridable: `ChargesConfig.equity_delivery(dp_charge_flat_sell=15.0)`.

```python
from qbeast_exec import ChargesCalculator, ChargesConfig

c = ChargesCalculator(ChargesConfig.equity_delivery())
c.breakdown("BUY", qty=100, price=1000.0)
# {'turnover': 100000.0, 'brokerage': 0.0, 'stt': 100.0, 'exchange': 2.97, 'sebi': 0.1,
#  'stamp_duty': 15.0, 'gst': 0.5526, 'dp_charge': 0.0, 'total': 118.6226, 'rate': 0.0011862}
c.schedule()          # side-by-side BUY/SELL table
```

**Verified effective rates** (zero-brokerage delivery): **BUY 0.1186%**, **SELL 0.1036%**, average
**0.1111%** — which is why a blended `0.0011` is a good single-constant approximation.

## 2b. `SlippageConfig` / `SlippageModel`

Four modes:

| `mode` | Behaviour | Needs OHLCV? |
|---|---|---|
| `"none"` | no slippage | no |
| `"fixed"` | flat `fixed_rate`, always adverse | no |
| `"rank"` | per-symbol rate by liquidity **rank**, `rank_min`..`rank_max` (legacy; universe-relative and static — **not recommended**) | volume only |
| `"dynamic"` | **recommended** — uncapped, size-aware, two-sided | yes |

**The dynamic model:**

```
μ[s,t] = max(CorwinSchultz/2, tick/(2·P))  +  Y · vol · √(Q/ADV)
         └── effective half-spread ─────┘     └─ square-root impact ─┘
A[s,t] = c_atr · ATR%[s,t]
p[s,t] = clip(0.5 + μ/(2A), 0.5, 1)          ← solves A(2p−1) = μ
ε      = +A with probability p, else −A       ← genuinely two-sided
fill   = reference × (1 + ε)  [BUY]  |  × (1 − ε)  [SELL]
```

Because `p` is solved from `μ`, **E[ε] = μ exactly** — the expected drag is the microstructure estimate,
while individual fills can come in better or worse. `p` is *emergent*, never an invented parameter.

| Field | Default | Meaning |
|---|---|---|
| `mode` | `"dynamic"` | see table above |
| `y_impact` | `0.5` | square-root impact coefficient (Almgren) |
| `c_atr` | `0.10` | `A = c_atr × ATR%` — the per-trade magnitude |
| `tick` | `0.05` | min price increment → the one-tick spread floor |
| `max_participation` | `0.10` | fills above this fraction of ADV are flagged **infeasible** |
| `participation_cap` | `1.0` | caps the rate used in the formula (avoids absurd impacts) |
| `cs_window`/`vol_window`/`adv_window`/`atr_n` | `21` | estimator windows |
| `seed` | `7` | draws are deterministic per (symbol, date, side, seed) |

```python
from qbeast_exec import SlippageModel, SlippageConfig

# frames = {"SYMBOL": DataFrame[open, high, low, close, volume]} indexed by date
slip = SlippageModel(frames, SlippageConfig(mode="dynamic", c_atr=0.10, seed=7))

slip.terms("RELIANCE", "2024-07-16", qty=1000, price=2900.0)
# {'mu':…, 'A':…, 'p':…, 'half_spread':…, 'impact':…, 'participation':…, 'feasible': True}

slip.fill_price("RELIANCE", "2024-07-16", "BUY", 1000, reference=2900.0)
slip.summary()      # realised adverse %, mean drag, p5/p95
slip.realised()     # every fill this model priced
slip.capacity(symbols, date, [1e7, 1e8, 1e9], top_n=20)   # the capacity curve
```

### Two design points that matter

1. **Anchor to the OPEN, not the live quote.** `reference_price(sym, date, fallback)` returns that day's
   open. Nautilus's quote at fill time is the *prior* bar's price, so perturbing it would fold the
   **overnight gap** into "slippage" — but the gap is real market movement already captured by trading at
   the open. Mixing them double-counts market risk as an execution cost.
2. **Corwin–Schultz is floored at one tick.** CS returns negative estimates in volatile periods (clipped
   to 0), i.e. the *tightest* spread exactly when spreads are *widest*. `tick/(2P)` is a physical bound.

### Capacity — what a rank-based model cannot tell you

Because μ contains an order-size term, `capacity()` answers *"what AUM can this strategy carry?"*:

```
portfolio    mean μ    untradeable
   ₹1 cr     0.22%        0/20
 ₹100 cr     0.50%        1/20
 ₹500 cr     0.79%       13/20      ← the strategy runs out of room here
```

## 2c. Nautilus adapters

```python
from qbeast_exec import NautilusFeeModel, NautilusFillModel, ChargesConfig

fee  = NautilusFeeModel(ChargesConfig.equity_delivery())
fill = NautilusFillModel(slip)          # slip = a SlippageModel

engine.add_venue(venue=VENUE, oms_type=OmsType.NETTING, account_type=AccountType.CASH,
                 base_currency=INR, starting_balances=[Money(10_000_000, INR)],
                 fee_model=fee, fill_model=fill,
                 allow_cash_borrowing=False, bar_execution=True)
```

Then, **in your Strategy**, tell the fill model which day it is:

```python
def on_bar(self, bar):
    fill_model.set_date(pd.Timestamp(bar.ts_event))    # ← required, once per bar
    ...
```

> **`set_date()` must run before the fills for that day.** Nautilus's matching engine can fill an order
> before the next bar's `on_bar` callback. The robust pattern (used in production here) is a **clock
> instrument** — feed an index bar with `offset_ns=0` and the tradeable instruments with `offset_ns=1`,
> and call `set_date()` on the index bar. It is then guaranteed to precede that day's stock fills.

> **⚠️ Never double-count.** If you use `NautilusFillModel` (price-based slippage), leave
> `NautilusFeeModel(..., extra_rate=0.0)`. Use `extra_rate` **only** for cost-based slippage without a
> fill model.

**Verified in a real `BacktestEngine`:** with `mode="fixed", fixed_rate=0.01`, a BUY on a bar whose open
was 200.00 filled at **202.00** (= 200 × 1.01). A quote-anchored model would have filled at 203.00 —
proving the open anchoring works. Commission matched `ChargesCalculator` to Nautilus's 2-dp `Money`
rounding.

---

# 3. Reporting module — `qbeast_exec.report`

### `performance_stats(nav, periods_per_year=252, rf=0.0) -> dict`

Returns: `start, end, years, final, total_return, cagr, vol, sharpe, sortino, max_dd, avg_dd,
avg_dd_episode, n_dd_episodes, pct_underwater, calmar, best_year, worst_year, hit_rate_monthly`.

**Three drawdown measures**, because they answer different questions:

| Metric | Question it answers |
|---|---|
| `max_dd` | worst single peak-to-trough |
| `avg_dd` | **time-weighted** mean of the underwater curve (days at a high count as 0) — *"on average, how far below peak?"* |
| `avg_dd_episode` | mean trough of each distinct episode — *"how deep does a typical drawdown get?"* |

An empty or single-point NAV returns NaNs — it never raises.

### `drawdown_table(nav, top=10) -> DataFrame`

The deepest episodes: peak, trough, end, depth %, duration, days-to-trough, recovered flag.

### `Dashboard` — self-contained HTML

| Input | Required | Adds |
|---|---|---|
| `nav` | **yes** | equity curve, drawdown chart, statistics |
| `benchmark` | no | benchmark line + a "vs benchmark" KPI |
| `tax` | no | FY tax statement + holding-period tabs |
| `slippage` | no | realised-slippage tab with adverse % and drag |
| `charges` | no | itemised charge-schedule tab |
| `trades` | no | fill log tab |
| `title`, `subtitle`, `periods_per_year` | no | presentation |

```python
from qbeast_exec import Dashboard

Dashboard(nav, benchmark=bench, tax=tax, slippage=slip,
          charges=ChargesCalculator(ChargesConfig.equity_delivery()),
          trades=fills_df, title="Momentum v3").write("reports/momentum_v3.html")
```

One HTML file, no external assets — email it, commit it, open it anywhere. Plotly is bundled if
installed; without it the tables still render.

---

# 4. Survivorship module — `qbeast_exec.survivorship`

**The bias this removes is the largest source of fake alpha we have measured.** Take today's index
constituents and backtest them over ten years: every one of them survived ten years. You selected the
winners using information from the end of the window, then measured how well the winners did.

Measured on a real panel here, that bias lifted a LightGBM trend model's t-statistic from **1.42 → 2.54**
while pushing genuine 12-1 momentum **down from 2.63 → 1.66**. It manufactures skill that is not there
and hides skill that is.

### Use it

```python
from qbeast_exec import Membership

memb = Membership("E:/data/Nifty100_membership.csv", available=set(prices.columns))

for t in calendar:
    for sym in memb.eligible(t):       # only names really in the index on t
        ...

print(memb.report(start=window_start))  # put this in the run log
```

### File format

One row per reconstitution. Extra columns are ignored, so exchange exports work as-is.

```csv
effective_date,symbols
2016-04-29,"ACC,ADANIENT,AMBUJACEM,..."
2017-04-28,"ACC,ADANIENT,APOLLOHOSP,..."
```

### API

| Call | Returns |
|---|---|
| `Membership(path, available=…, config=…, extra_dirs=…)` | the control; `path=None` is an explicit pass-through |
| `.eligible(date)` | constituents on that date **∩** symbols you have prices for |
| `.members(date)` | constituents, before intersecting with your prices |
| `.enabled` / `.first_date` / `.last_date` | is a real control in force, and over what span |
| `.audit(start, end)` | machine-readable dict — **assert on this in CI** |
| `.coverage()` | DataFrame: members vs priced vs `coverage_pct`, per reconstitution |
| `.missing(date=None)` | constituents you have no prices for |
| `.report(start, end)` | a block for the run log; says "SURVIVORSHIP BIASED" when off |
| `assert_no_look_ahead(memb, start)` | raises unless the window starts on/after the first reconstitution |
| `SurvivorshipConfig(alias=…, junk=…, strict=…)` | ticker aliases, feed artefacts, and whether a missing file raises |

### Three guards, and why each exists

**A missing file raises — it does not warn.** A warning scrolls past in a wall of backtest output and
the run still prints a headline that reads as bias-controlled. Pass `SurvivorshipConfig(strict=False)`
when you genuinely intend to run without the control.

**A window starting before the first reconstitution is reported as look-ahead.** The usual
implementation silently reuses the earliest list for every earlier date — which is the bias again,
confined to the years nobody re-checks. Use `assert_no_look_ahead()` in your integrity checks:

```python
from qbeast_exec import assert_no_look_ahead
assert_no_look_ahead(memb, BACKTEST_START)      # raises, with the correct start date in the message
```

**`coverage()` quantifies the partial fix.** A constituent you have no prices for is still missing from
the eligible set, so point-in-time control is only as complete as your history. State it:

```
[survivorship] ON  - 36 reconstitutions, 2013-04-01 .. 2026-09-14
               coverage 94.8% (189 of 200 constituents priced, on average)
               24 constituents never priced (delisted names you lack history for)
```

### Aliases

Index feeds and data folders spell tickers differently. `DEFAULT_ALIAS` maps the ones we have hit;
entries marked `(*)` in the source are **merger proxies** (the surviving entity), not clean renames —
a judgement call, which is why they are an overridable default rather than buried in the parser.

```python
cfg = SurvivorshipConfig(alias={**DEFAULT_ALIAS, "OLDNAME": "NEWNAME"})
```

---

# 5. Universe reporting — `qbeast_exec.universe`

A strategy is never run on one universe. Run it on large caps, then mid caps, then small caps, and the
outputs land on top of each other until nobody can say which dashboard belongs to which run — and the
summary numbers get compared directly, as though a small-cap CAGR and a large-cap CAGR answered the
same question.

### Use it

```python
from qbeast_exec import UniverseReport

rep = UniverseReport("reports", "smallcap250_ddq")      # -> reports/smallcap250_ddq/
nav.to_csv(rep.path("equity.csv"))
dash.write(rep.path("dashboard.html"))

rep.save(stats, benchmark=bench_stats, window=(start, end), gate="regime", engine="nautilus")
```

Then, across every universe you have run:

```python
print(UniverseReport.table("reports"))
df = UniverseReport.compare("reports")                  # DataFrame, one row per universe
```

### API

| Call | Returns |
|---|---|
| `UniverseReport(root, universe)` | the folder handle; name is lower-cased, folder created |
| `.dir` / `.path(*parts)` | the folder / a path inside it, parents created |
| `.save(stats, benchmark=…, window=…, **meta)` | writes `summary.json`, computing the excess columns |
| `.load()` | that summary, or `{}` if never run |
| `UniverseReport.universes(root)` | every universe with a stored run, newest first |
| `UniverseReport.compare(root, …)` | one row per universe, benchmark-relative columns first |
| `UniverseReport.table(root, …)` | the same as text, with the caveat attached |

It is `os.PathLike`, so `open(rep, …)` and `Path(rep)` work directly.

### The rule it enforces

**Never quote an absolute return across universes as if comparable.** A small-cap book in a small-cap
decade and a large-cap book in a large-cap decade are different questions. So `compare()` leads with
`excess_cagr_pp` and `dd_better_pp` — those mean the same thing everywhere — and `table()` prints the
caveat rather than leaving you to remember it:

```
  excess_cagr_pp / dd_better_pp are benchmark-relative and ARE comparable across universes.
  cagr / benchmark_cagr / final are NOT: each universe had a different decade.
```

A universe with no stored run is **absent** from the comparison rather than shown as blanks, so a
missing run looks missing instead of looking like a zero.

---

# 6. Price bands / circuit locks — `qbeast_exec.bands`

A stock locked at its circuit trades on one side only: you cannot buy an upper lock or sell a lower one.

```python
from qbeast_exec import PriceBandModel, BandConfig

bands = PriceBandModel(frames, BandConfig())
if bands.check(symbol, date, "BUY", qty):        # every refusal is recorded for audit
    ...place the order...
print(bands.summary())
```

**Check at submission, not at fill.** Nautilus falls back to standard fill logic when a custom
`FillModel` returns an empty book, so a lock cannot be enforced by withholding liquidity.

**A naive `high == low` test over-counts locks by roughly 1000×.** The model requires flat **and**
`|return| ≈ that symbol's own band` **and** a price floor. Measured reality on Indian equities: 19 real
locks in 1,451,626 symbol-days (0.0013%).

## The staircase flag — `StaircaseFlag`

A stock climbing its upper circuit in steps — **+band%, +band%, +band%** — is an operator ladder, not
price discovery. Micro caps are where it lives.

**It matters because momentum ranks these names top.** A run of locked up-sessions is, arithmetically,
the strongest momentum in the universe, so a momentum strategy wants to buy exactly the thing whose
price is not real and whose upper lock has no sellers anyway. The circuit gate refuses the individual
locked session; this refuses the **name**, for as long as the pattern persists.

```python
from qbeast_exec import StaircaseFlag, StaircaseConfig

sc = StaircaseFlag(frames, StaircaseConfig(window=20, min_hits=3, min_run=3))
if sc.check(sym, date, "BUY", qty):        # every refusal recorded, with its evidence
    ...place the order...

print(sc.report(date, held=set(portfolio)))   # what is flagged, and which you already hold
sc.scan().to_csv("staircase_episodes.csv")    # every ladder in the loaded history
```

| Call | Returns |
|---|---|
| `.flagged(symbol, date)` | is the ladder live right now |
| `.steps(symbol, date)` | `(hits in window, consecutive run)` — the evidence |
| `.check(symbol, date, side, qty)` | allowed? refusals recorded with hits/run/band |
| `.flagged_symbols(date)` / `.table(date, held=…)` | what is flagged, worst first; `held` marks yours |
| `.blocked_table()` / `.summary()` / `.scan()` | refusals, totals, every historical episode |

**BUY is refused; SELL stays open.** If you already hold one, you want the exits available — the flag
stops you *adding*, it does not trap you.

**`require_lock` defaults to False, and that is measured, not assumed.** On 888,022 micro-cap
symbol-days: 531 closes at/above the upper band (0.060%), but only **22** with zero intraday range
(0.0025%) — and those yield a maximum run of **2**, so demanding an exact lock makes the detector
unreachable. The tradeable signature is the *return* pattern, not a perfectly flat bar, which is an
artefact of feed rounding. Set it True only if your feed really prints `high == low` on locked sessions.

> **Know what it will do on your data before you rely on it.** Run `.scan()` first. On NIFTY
> MICROCAP250 the whole history contains **5 ladders, the longest 4 sessions** — all in the 2008 and
> 2020 rebounds, which look more like recovery rallies than manipulation. It is a cheap safety rail
> there, not a return driver.

Market-wide halts are separate — `MarketWideBreaker`, sourced from NIFTY 50 or SENSEX, not your traded
benchmark. Its real value is `stress()`: synthetic injection, because the risk path is barely exercised
by historical replay (4 triggers in 6,626 sessions).

---

# 7. Nautilus books — `qbeast_exec.books`

Turns any finished Nautilus engine into the four artefacts a desk actually reads.

```python
from qbeast_exec import export_books
export_books(engine, "backtest", out_dir, order_dates=order_dates, opens=opens)
# -> orderbook_backtest.csv, tradebook_backtest.csv,
#    book_by_stock_backtest.csv, book_summary_backtest.txt
```

`order_dates` maps `client_order_id -> decision date`, so the order book records **when the order was
decided (t)** as well as when it filled (t+1). Without it you cannot audit the next-day-open rule.

**`opens` is not optional in practice — pass it.** It is a DataFrame of opens indexed by date with
symbols as columns. Under bar execution Nautilus matches a market order against the newest bar it has
processed, which is the **previous session's**, so both books come out carrying a price that was never
executable — and in a momentum book that price is systematically favourable. With `opens` both
`orderbook.avg_px` and `tradebook.price` are repriced to the **fill-day open**, which is what the order
could actually have transacted at, and the books then agree with a ledger built from the same fills.
Omit it and you get a warning-free export of unexecutable prices.

Rows whose symbol or date is absent from the panel keep the price they came with, so a partial panel
degrades rather than corrupting. Orders with `filled_qty == 0` are never repriced — a rejected order's
`avg_px` stays `0` instead of being stamped with a real price.

---

# 7b. The ledger and the T → T+1 contract — `qbeast_exec.ledger`, `qbeast_exec.nautilus`

## Why these exist

`export_books` turns a finished engine into readable books and `NautilusFeeModel` charges it correctly,
but neither answers the question a strategy actually needs answered: **what is my NAV?**

**Nautilus models neither capital-gains tax nor a yield on idle cash.** Its account equity is therefore
a pre-tax, interest-free number. Reading it and calling it a result overstates return — and because
position sizing reads equity, it silently levers the book. On one real run that was worth **~3pp of
unfunded leverage and 485 negative-cash days** before it was caught.

So the overlay has to live somewhere, and it lives here. With these two modules the package is enough
to run a Nautilus strategy end to end.

## The T → T+1 contract

A daily strategy decides on day `t`'s close and can only trade at `t+1`'s open. Under bar execution
that goes wrong in **two independent ways that look identical in the output**:

| Failure | What happens | How visible |
|---|---|---|
| **Wrong day** | an order submitted on `t`'s bar fills on `t`, so the decision reads a price it then trades at | a date audit catches it |
| **Wrong price** | an order submitted before the session's own bar is matched against the newest bar the engine *has* — the **previous session's** | **invisible** in a date audit |

The second is the dangerous one: it affects ~**99.9%** of fills, and in a momentum book it is
systematically **favourable**, because the book buys names that gapped up. Measured on a real run
before it was fixed: a mean **+0.25%** discount per fill, **₹84,728** of return that did not exist.

**Three pieces enforce the contract, and all three are needed:**

```python
from qbeast_exec import make_equity, make_bars, DeferredSubmit, replay_fills

# 1. BAR ORDER — the clock instrument first, tradables one nanosecond later
clock = make_equity("NIFTY 50", venue="XNSE")
engine.add_data(make_bars(clock, ic, ic, ic, ic, None, offset_ns=0))      # FIRST
for s in symbols:
    inst = make_equity(s, venue="XNSE")
    engine.add_data(make_bars(inst, opens[s], None, None, closes[s], vols[s], offset_ns=1))

# 2. DEFERRAL — decide on t, submit on t+1
def on_start(self):
    self.defer = DeferredSubmit()

def on_bar(self, bar):
    if bar.bar_type.instrument_id != clock.id:
        return                                   # only the clock bar drives the schedule
    for day, target in self.defer.release():     # at most the PRIOR day's target
        self.submit_target(day, target)
    if day in rebalance_dates:
        self.defer.queue(day, target_weights)    # fills NEXT bar = t+1 open

# 3. REPRICE — correct the price Nautilus assigned
led = replay_fills(fills, closes, cal, initial, opens=opens,
                   tax_config=TaxConfig(), charges_config=ChargesConfig.equity_delivery(),
                   cash_yield=0.04)
```

`offset_ns` is the mechanism, not a cosmetic detail. Nautilus processes same-timestamp data in feed
order, so the clock bar takes `0` and every tradable takes `1`. An order submitted on the clock bar then
meets the day's own tradable bar and fills at its **open**. Give them the same offset and the fill price
depends on feed order — which is to say it is undefined.

## `DeferredSubmit`

A deliberately tiny, engine-free state machine. It is the one piece of the contract testable without
Nautilus, and the piece most likely to be reimplemented wrongly. Its invariant: **nothing is ever
submitted on the bar it was decided on.**

```python
d = DeferredSubmit()
d.queue(day_t, target)        # decided on t
list(d.release())             # []  on t   -- nothing submitted same-bar
list(d.release())             # [(day_t, target)]  on t+1
```

`queue(day, {})` is legitimate and means **flatten to cash**. An earlier version dropped falsy targets,
so the engine was never told to de-risk in a downtrend: it rode through crashes and drawdown went from
−17.5% to −35%. `queue(day, None)` raises — that is a programming error, not an instruction.

## `Ledger`

A transparent cash-account simulation: integer positions, no shorting, no leverage. Every rupee of
commission, slippage and tax hits cash, so the equity curve is net of everything. Capital gains go
through `TaxEngine` — the same FIFO lot book and statutory rules as everywhere else.

```python
from qbeast_exec import Ledger, TaxConfig, ChargesConfig

led = Ledger(1_000_000, tax_config=TaxConfig(),
             charges_config=ChargesConfig.equity_delivery(), cash_yield=0.04)
led.mark(date, close_row)        # accrue yield, assess FY tax, mark to market -- once per session
led.equity_curve()               # the NAV series
led.lot_closes                   # prove the FIFO matching
led.tax_events                   # per-FY assessments
```

**Config-free by construction.** Every rule arrives as an explicit config object; there is no
module-level import of any project settings, because a library that reads someone's `config.py` is not
a library. It also makes sweeps safe — rewriting a config file between runs cannot leave this holding a
stale value.

`tax=False` runs the **same** fills with the capital-gains deduction off. Identical trades, so the
difference between the two curves is exactly the tax drag and nothing else.

## `replay_fills`

```python
led = replay_fills(fills, closes, cal, initial, opens=opens, ...)
```

`fills` is a sequence of `dict(ts=, symbol=, qty=, px=, comm=)` — exactly what an `on_order_filled`
handler collects. Sells are applied before buys within a day, so proceeds fund purchases and cash never
dips negative on a day that is actually self-financing.

**Pass `opens`.** Without it the fills keep the engine's price — the previous session's close — and you
get a warning-free replay of unexecutable prices. With it every fill is repriced to the fill-day open.
Nautilus still decides **which** orders fill and **when**; only the price is corrected.

> **Parity.** This ledger was ported from a project implementation and verified **bit-identical** on a
> real 2007–2026 fill sequence: 4,547 sessions, 3,494 fills, max |NAV difference| **₹0.0000000000**,
> with tax, interest, cost, cash, trade count, tax events, lot closes and final positions all matching
> exactly.

---

# 8. End-to-end example

```python
import pandas as pd
from qbeast_exec import (TaxEngine, ChargesConfig, ChargesCalculator, NautilusFeeModel,
                         SlippageModel, SlippageConfig, NautilusFillModel, Dashboard)

# --- 1. models (share these across the team) --------------------------------
charges = ChargesConfig.equity_delivery(dp_charge_flat_sell=15.0)
slip    = SlippageModel(ohlcv_frames, SlippageConfig(mode="dynamic", c_atr=0.10, seed=7))
fee_m   = NautilusFeeModel(charges)
fill_m  = NautilusFillModel(slip)

# --- 2. run YOUR Nautilus backtest with them --------------------------------
engine.add_venue(..., fee_model=fee_m, fill_model=fill_m, bar_execution=True)
#   in your Strategy.on_bar:  fill_m.set_date(pd.Timestamp(bar.ts_event))
engine.run()

# --- 3. tax the resulting fills ---------------------------------------------
fills = pd.DataFrame([{ "symbol": f.instrument_id.symbol.value,
                        "date":   pd.Timestamp(f.ts_event),
                        "qty":    float(f.last_qty) * (1 if f.order_side == OrderSide.BUY else -1),
                        "price":  float(f.last_px) } for f in my_fill_events])
tax = TaxEngine()
tax.apply_fills(fills)
tax_by_year = tax.assess_all()

# --- 4. one dashboard -------------------------------------------------------
Dashboard(nav, benchmark=bench, tax=tax, slippage=slip,
          charges=ChargesCalculator(charges), trades=fills,
          title="My Strategy").write("reports/my_strategy.html")
```

**Deducting tax from NAV** is the caller's job (the engine reports what is *due*):

```python
for t in calendar:
    cash -= tax.assess(t)          # fires once per FY, at/after 31-Mar
```

---

# 9. Choosing settings — team conventions

| Question | Recommendation |
|---|---|
| Which slippage mode? | `"dynamic"`. Use `"fixed"` only for a quick sanity run, `"rank"` never for new work. |
| `c_atr`? | `0.10`. Raise to `0.15–0.20` for a stress scenario. It sets *dispersion*, **not** expected drag. |
| Does `c_atr` change my P&L? | **Essentially no.** E[ε] = μ regardless of `c_atr`. Only **μ** drives the drag. |
| Charges preset? | `equity_delivery()` for positional; add `dp_charge_flat_sell=15.0` for small books. |
| `carry_forward_losses`? | `True` is more accurate; `False` (default) is conservative. **Say which you used.** |
| `cess`? | `0.04` for a realistic post-cess figure; `0.0` for base statutory rates. |
| Reporting drawdown? | Quote `max_dd` **and** `avg_dd` and `pct_underwater` — max alone flatters a strategy that spends most of its life underwater. |

**For comparability across the team, agree on one profile and state it.** Suggested default:

```python
TaxConfig()                                   # base statutory, no carry-forward, no cess
ChargesConfig.equity_delivery()               # zero brokerage, ≈0.111% per side
SlippageConfig(mode="dynamic", c_atr=0.10, y_impact=0.5, seed=7)
```

---

# 10. Version & verification

`qbeast_exec.__version__` = **1.1.1**

```bash
python -m qbeast_exec.selftest      # 190 assertions, all hand-computed → "190 passed, 0 failed"
```

Covered: all four tax rate/regime combinations, cross-bucket netting, FIFO splitting across the 365-day
boundary, loss years, carry-forward on/off, cess, FY boundaries, assess-once semantics, oversell
rejection, every charge component for delivery/intraday/blended, DP charges, slippage two-sidedness,
determinism, size- and liquidity-monotonicity, the participation guard, `E[ε] = μ`, capacity monotonicity,
and all drawdown metrics against a hand-built curve.

Survivorship: as-of lookup on and between reconstitution dates, unpriced constituents excluded from
`eligible()` but present in `members()`, coverage arithmetic, look-ahead detection and the day count,
`assert_no_look_ahead` raising, a missing file raising under `strict=True` and not under `strict=False`,
pass-through mode declaring itself biased, and alias/junk handling.

Universe reporting: name normalisation, folder and nested-path creation, excess and drawdown-delta
arithmetic, metadata round-trip, column ordering, unrun universes excluded from the comparison, the
not-comparable caveat present, and an empty root returning empty.

Fill repricing (`reprice_to_open`): a stale row moving to the fill-day open, an already-correct row left
alone, a symbol absent from the panel keeping its price, `opens=None` as a no-op, a **missing date column
raising** rather than silently repricing nothing, tz-aware UTC timestamps matching a tz-naive panel, and
the masked order-book case — an unfilled order keeping `avg_px` 0, with a companion assertion showing
that *without* the mask it would be stamped with a real price.

The T → T+1 contract (`DeferredSubmit`): nothing released on the queueing bar, exactly one release on
the next, idempotent re-release, an **empty target surviving** as "flatten to cash", `None` raising, and
re-queueing replacing rather than duplicating.

Ledger: cash after a buy, position open/close with key removal, NAV as cash + mark-to-market, realised
gain recorded, one day of cash yield to the rupee, gross-vs-net on identical fills, and `replay_fills`
repricing through to the trade record.

**Parity:** the ledger was verified **bit-identical** to the project implementation it was ported from
on a real 2007–2026 sequence — 4,547 sessions, 3,494 fills, max |NAV difference| **₹0.0000000000**, with
tax, interest, cost, cash, trade count, tax events, lot closes and final positions all matching.
