# Handoff — running your own strategy on this framework

You have been sent a backtest framework where **only the strategy changes**. Data loading,
tradability, point-in-time membership, the staircase gate, charges, slippage, capital-gains tax and
execution are shared and already certified. You write one file.

---

## What you received

```
src/                 the two packages: qbeast_backtest (the stages) + qbeast_exec (costs/tax/engine)
pyproject.toml       the install
README.md            architecture, the contracts, what a run writes
docs/                ARCHITECTURE.md, RUNNING.md, STRATEGY_GUIDE.md
HANDOFF.md           this file
```

About 60 files, under 1 MB. **No data** — see step 2.

---

## 1. Install

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -e .
pip install -e ".[nautilus]"      # optional: the Nautilus execution engine (see step 5)
```

Python 3.10+. The internal ledger needs no Nautilus and is the fast path every research sweep uses.

Check it:

```bash
qbeast-backtest-selftest          # 47 contract tests
qbeast-exec-selftest              # 211 cost/tax/slippage tests
```

Both should pass before you write anything. If they do, the shared layers are intact and any later
failure is yours, which is the point of running them first.

## 2. Point it at your data

One environment variable:

```bash
set QBEAST_DATA_ROOT=D:\your\market_data        # Windows
export QBEAST_DATA_ROOT=/mnt/market_data        # POSIX
```

Every universe preset hangs off it. The layout it expects, per universe:

```
<QBEAST_DATA_ROOT>/
    Final_NIFTY100_EoD_Data/            one CSV per symbol + the index CSV
        RELIANCE.csv ... NIFTY 100.csv
    Final_Indices_Data/                 index series (NIFTY 50/100/200/500 ...)
    Survivorship_Bias/                  point-in-time membership CSVs (optional)
```

Each symbol CSV needs `date,open,high,low,close,volume`, dates as `DD-MM-YYYY`
(`config.DATE_FORMAT`). The index CSV's **filename must equal `index_symbol`** in the preset — that
is checked, because getting it wrong once silently wrote MidCap prices into a file named "NIFTY 100"
and corrupted both the benchmark and the trend gate.

```bash
python -m qbeast_backtest.universes          # list every preset and validate it against your disk
```

If your folders are named differently, add a preset in `src/qbeast_backtest/universes.py` — copy a
block, change the four fields. Don't edit the paths one by one.

**Without a membership file**, set `USE_MEMBERSHIP = False` in `config.py`. The framework
*refuses* to run with membership requested but missing, rather than quietly backtesting on today's
index and reporting it as survivorship-controlled.

## 3. Write your strategy

One file, `src/qbeast_backtest/stage2_strategy/my_strategy.py`:

```python
from __future__ import annotations

import pandas as pd

from qbeast_backtest.contracts import MarketData, StrategyOutput

from .base import StrategyBase


class MyStrategy(StrategyBase):
    name = "my_strategy"

    def decide(self, md: MarketData) -> StrategyOutput:
        from qbeast_backtest import config as C

        # Trailing signals on FULL history, read on the calendar. See the warm-up rule below.
        sig = md.history_closes.pct_change().rolling(60, min_periods=40).std()
        sig_c = sig.reindex(md.calendar).ffill()

        targets, expo = {}, {}
        for t in self.decision_sessions(md):          # NOT md.rebalance_dates - see below
            elig = self.eligible_on(md, t)            # membership + staircase already applied
            row = sig_c.loc[t].dropna()
            pick = list(row[[s for s in row.index if s in elig]].nsmallest(int(C.TOP_N)).index)
            targets[t] = {s: float(C.CASH_BUFFER) / len(pick) for s in pick} if pick else {}
            expo[t] = 1.0 if pick else 0.0

        return StrategyOutput(targets=targets, exposure=pd.Series(expo).sort_index(),
                              name=self.name, params=dict(top_n=int(C.TOP_N)))
```

Add one line to `src/qbeast_backtest/stage2_strategy/registry.py`. That is the whole integration.

### What you may read

`MarketData` and nothing else. If you need an input it does not carry, add it in **stage 1**, where
every strategy gets it on one convention — not privately in your own module.

| field | what it is |
|---|---|
| `calendar` | the sessions this run trades on |
| `closes` / `opens` / `volumes` | on the calendar, forward-filled where correct |
| `history_closes` / `history_volumes` | from the start of the data — **compute trailing signals here** |
| `benchmark` / `signal_index` | the measurement series, and what a trend gate reads |
| `eligible[t]` | the names you may hold on session `t` |
| `rebalance_dates` | when you decide |

### Four rules, all enforced at the boundary

1. **`targets[t]` must sum to at most `CASH_BUFFER`.** More is leverage nobody authorised, and the
   run fails rather than taking it.
2. **Long-only.** Negative weights are rejected.
3. **You may only hold names in `eligible[t]`.** That set is point-in-time membership minus the
   staircase gate minus anything untradeable. Ranking outside it fails at the boundary — this is what
   makes survivorship control and the staircase binding rather than advisory.
4. **An empty dict is a flatten, a missing key is a bug.** `targets[t] = {}` means *go to cash*;
   omitting `t` means you did not decide. The framework distinguishes them.

### Two mistakes that cost real money here

**Compute trailing signals on `history_closes`, not `closes`.** `closes` is the trading window.

> The first version of this contract shipped only the window. 12-1 momentum had nothing to warm up
> on, the first fully-ranked session slipped **371 days**, the book held almost nothing for a year,
> and the run finished at **Rs 1.04M against the correct Rs 3.12M**. Nothing errored.

**Loop over `self.decision_sessions(md)`, not `md.rebalance_dates`.** In a backtest a decision on the
final session has no next open to fill at and must be skipped; in live/paper trading that session is
the *only* one that matters. `MarketData.last_session_fillable` carries the difference and
`decision_sessions()` reads it. Hand-rolling `if t == md.calendar[-1]: continue` is right for a
backtest and silently produces a paper book that never places an order.

## 4. Run it

```bash
qbeast-backtest --strategy my_strategy
qbeast-backtest --strategy my_strategy --engine internal --no-dashboard     # fast iteration
qbeast-backtest --list                                                      # registered strategies
```

Change the universe with one word — `UNIVERSE` in `src/qbeast_backtest/config.py`.

## 5. What you get

In `reports/<universe>/`:

| file | |
|---|---|
| `dashboard.html` | the fund dashboard: cumulative return, drawdown vs the index, idle cash, gross-vs-after-tax, regime map, calendar-year and monthly returns, the rebalance table, signal grid, trades, config, audit |
| `equity.csv` | NAV, pre-tax NAV, invested, cash, deployment %, drawdown %, benchmark |
| `summary.json` | your headline vs the benchmark, plus the friction split |
| `tradebook_backtest.csv` | every fill, with charges and slippage |
| `staircase/` | what the operator-ladder gate flagged and whether it changed the book |

The dashboard has 13 tabs: Overview, Returns (tax), Regime map, Index, Year by year, Monthly returns,
Rebalance, Signal grid, Trend prediction, Feature selection, Trades, Configuration, Logs. Every chart
labels itself from your strategy's `name` and `params`.

Two tabs read from `StrategyOutput.diagnostics` if you publish the columns, and are omitted otherwise:

| publish | and you get |
|---|---|
| `diagnostics["posture"]` (or `StrategyOutput.posture`) — `full` / `sized` / `cash` | the **Regime map** shaded by posture, over your deployment |
| `diagnostics["level"]` — whatever curve you gate, rank or size on | your line overlaid on the benchmark candles in **Index** |
| `diagnostics["mom"]` — your selection signal | the **Signal grid**, the universe ranked by it |

That is the generic replacement for the removed reference strategy's own overlays: it drew its trend
gate on the candles and its regime bands on the map. Publish your equivalents and the same charts
describe your strategy instead.

With `--engine nautilus` you also get `orderbook_backtest.csv`, `book_by_stock_backtest.csv`,
`book_summary_backtest.txt` and `nautilus_nav.csv`. **The order book is Nautilus-only** — the internal
ledger has no order objects, only fills, so the trade book is built from the ledger instead.

The two engines differ by design: Nautilus sizes on the prior close (what a market-on-open order can
actually know), the internal ledger on the open it fills at. On the reference universe that is about
0.08pp of CAGR. Internal for iteration, Nautilus as the source of record.

## 6. Before you believe a result

```bash
qbeast-backtest-selftest          # the contracts still hold
```

And read your own `summary.json`: `survivorship` must be `true` if you meant it to be, and
`deployment` in `equity.csv` tells you whether a flat NAV means "held nothing" or "held things that
did not move". A strategy that looks excellent while 90% in cash has not been tested on much.

Costs, slippage and tax are **not** optional knobs you tune to taste — they are the certified shared
layer, and a result quoted without them is not comparable to anything else in this project.

---

## Where to look next

| question | file |
|---|---|
| how the three stages fit together | `README.md`, `docs/ARCHITECTURE.md` |
| the contract field by field | `src/qbeast_backtest/contracts.py` |
| the contract a strategy implements | `src/qbeast_backtest/contracts.py` (`StrategyOutput`) and `stage2_strategy/base.py` |
| every CLI flag | `docs/RUNNING.md` |
| costs, tax, slippage, bands | `src/qbeast_exec/` + its README |
