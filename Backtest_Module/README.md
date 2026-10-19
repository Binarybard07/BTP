# Backtest_Modular — three modules, one strategy slot


**Documents in this folder**

| | |
|---|---|
| [HANDOFF.md](HANDOFF.md) | **start here** — install, point it at your data, write your strategy |
| [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md) | file by file, function by function, with the code quoted |
| [STAIRCASE_v2.4.1.md](STAIRCASE_v2.4.1.md) | the tradability gate: what it blocks and what it cost |
| `audit_reference.py` | checks every claim in TECHNICAL_REFERENCE.md against the tree |

A re-layering of `Backtest_Core` so that **a new strategy changes one module and nothing else**. Data,
tradability, costs, tax and the execution engine are shared and identical for every strategy, which is
the only condition under which two strategies can be compared at all.

**It reproduces `Backtest_Core` to the rupee.** `reconcile.py` is the acceptance test:
**Rs 0.0000 maximum difference on BOTH engines**, 0 of 2,498 sessions differing (smallcap250).

Installed as two packages: `qbeast_backtest` (the framework) and `qbeast_exec` (the shared team
library - charges, slippage, tax, bands, the staircase gate, survivorship, the Nautilus harness).
`qbeast_exec` is top-level so a paper book or live adapter can import it knowing nothing about this
framework.

---

## The three modules

```
src/qbeast_backtest/
stage1_data/          WHAT ARE THE PRICES, AND WHAT MAY BE TRADED
  loader.py             OHLCV, benchmark, signal index, calendar, full history for warm-up
  tradability.py        point-in-time membership  -  staircase-flagged names  =  ELIGIBLE SET
  staircase_log.py      the audit trail: run / rebalance / order
        |
        |  contracts.MarketData        (validated at the boundary)
        v
stage2_strategy/      THE ONLY PART A NEW STRATEGY CHANGES
  base.py               StrategyBase: eligible_on(), self_check()
  <yours>.py            YOUR strategy - this build ships none; see HANDOFF.md
  registry.py           name -> class
        |
        |  contracts.StrategyOutput    (validated at the boundary)
        v
stage3_execution/     COSTS, TAX AND THE EXCHANGE
  runner.py             the T->T+1 loop, internal ledger and NautilusTrader
        |
        |  contracts.ExecutionResult
        v
qbeast_exec/          THE SHARED TEAM LIBRARY (charges, slippage, tax, bands, staircase, nautilus,
                      survivorship, reporting) - 211 tests, one implementation, every strategy
```

`pipeline.py` is the only orchestrator. `shared/` holds the certified maths — `signals.py`,
`strategy.py`, `portfolio.py`, `engine.py`, `data_io.py`, `membership.py`, `breaker.py` — **copied
unchanged**. This refactor re-draws boundaries; it does not rewrite arithmetic.

---

## Adding a strategy

```python
# stage2_strategy/my_strategy.py
from contracts import MarketData, StrategyOutput
from .base import StrategyBase

class MyStrategy(StrategyBase):
    name = "my_strategy"

    def decide(self, md: MarketData) -> StrategyOutput:
        targets = {}
        for t in md.rebalance_dates:
            elig = self.eligible_on(md, t)        # survivorship + staircase already applied
            # ... your ranking, on md.history_closes for anything trailing ...
            targets[t] = {sym: weight, ...}
        return StrategyOutput(targets=targets, name=self.name)
```

Register it in `registry.py`, then `qbeast-backtest --strategy my_strategy`. Nothing in stage 1 or
stage 3 changes.

**The paper book uses the same slot.** `paper_trading/` has the same three stages against the same
contracts, so setting `STRATEGY = "my_strategy"` in `paper_trading/ptconfig.py` paper-trades the class
you just backtested — one definition, both paths. See
[paper_trading/STRATEGY_SLOT.md](paper_trading/STRATEGY_SLOT.md).

Loop over **`self.decision_sessions(md)`**, not `md.rebalance_dates`. In a backtest a decision on the
final session has no next open to fill at and must be skipped; a live book's newest session *is* the
final one and is the only decision that matters. `MarketData.last_session_fillable` carries the
difference. Hand-rolling `if t == md.calendar[-1]: continue` is correct for a backtest and silently
produces a paper book that never places an order.

### Two rules, both enforced at the boundary

1. **Read only what `MarketData` gives you.** If you need an input it does not have, add it to
   stage 1 — where every strategy gets it on one convention — not privately in stage 2.
2. **Compute trailing signals on `md.history_closes`, not `md.closes`.** `closes` is the trading
   window; `history_closes` runs from the start of the data. This is not style:

   > The first version of this contract shipped only the window. 12-1 momentum then had nothing to
   > warm up on, the first fully-ranked session slipped **371 days** (2016-09-30 → 2017-10-06), the
   > book held almost nothing for its first year, and the run finished at **Rs 1.04M against the
   > monolith's Rs 3.12M**. Nothing errored. `MarketData.validate()` now refuses a run with less than
   > `WARMUP_BARS` of history.

`StrategyOutput.validate()` will fail a strategy that returns weights summing above `CASH_BUFFER`
(unauthorised leverage), negative weights (long-only), targets off-calendar, or — the one that matters
here — **any name outside the eligible set**. That last check is what makes the staircase gate and
point-in-time membership binding rather than advisory.

---

## The staircase change

The gate existed and was switched on, but was consulted **only at fill time**:

```
before:  rank all -> top 20 -> order -> ledger refuses the buy  -> 19 names, idle cash
after:   flag -> remove from pool -> rank -> top 20 -> order    -> 20 names, fully deployed
```

Refusing the fill protects you from *owning* the thing. Removing it from the candidate pool also lets
you own something else instead. This matters most for a momentum ranker, because a run of locked +5%
sessions is arithmetically the strongest momentum in the universe — so the signal and the gate point
in opposite directions, and whichever runs **last** wins. It now runs first.

The ledger check is **kept as a backstop**. In a correct run it refuses zero buys; a non-zero count
means something reached execution without going through stage 1, which is worth knowing.

### Two switches, one of them deliberately off

| setting | default | why |
|---|---|---|
| `STAIRCASE_EXCLUDE_FROM_POOL` | `True` | the fix above |
| `STAIRCASE_BLOCK_SELL` | **`False`** | asymmetric. An **upper** circuit lock has no sellers — demand is what defines the state — so it is a name you can always exit. Refusing the exit traps capital in the one security you have just identified as an operator ladder rather than a market. `qbeast_exec`'s own default agrees. Available because it was asked for; off because the asymmetry is real. |

### The log

`reports/<universe>/staircase/` — written every run:

| file | one row per | what it answers |
|---|---|---|
| `run.json` | run | settings, totals, every symbol ever flagged |
| `flagged.csv` | session × symbol | hits, run, band, close, removed? |
| `rebalance.csv` | rebalance | removed names, **and whether that changed the book** |
| `blocked.csv` | order | every buy/sell the ledger backstop refused, with the reason |
| `episodes.csv` | episode | the gate's own scan |

`rebalance.csv`'s **`mattered`** column is the point. "The gate fired" and "the gate mattered" are
different claims, and only the second is interesting: a flagged name that would have ranked 47th was
never going to be bought. For each removal the pool is re-ranked with the name put back, and
`mattered` is True only if it would have made the top N.

---

## Commands

```bash
pip install -e .                   # or: pip install -e ".[nautilus]"

qbeast-backtest                    # the full run: stage 1 -> 2 -> 3, then the result table
qbeast-backtest --list             # registered strategies
qbeast-backtest --engine internal  # the fast path (no NautilusTrader needed)
qbeast-backtest --no-staircase     # A/B the gate
qbeast-backtest-selftest           # the contract conformance suite (27 checks)
qbeast-exec-selftest               # the shared library's arithmetic (211 checks)
qbeast-backtest-reconcile          # PROVE it still reproduces Backtest_Core (must stay Rs 0.00)
qbeast-backtest-reconcile --effect # measure what the staircase change does
```

Full detail: **[docs/RUNNING.md](docs/RUNNING.md)**. How to write a strategy:
**[docs/STRATEGY_GUIDE.md](docs/STRATEGY_GUIDE.md)**. Why there are four execution-related files and
what Nautilus does and does not handle: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

### Configuration

`src/qbeast_backtest/config.py` is **this package's own** and the only place its settings live. It
began as a copy of `Backtest_Core/config.py` and is now independent: nothing pulls from that folder,
and the package runs with `Backtest_Core` absent or deleted. Env overrides
(`QBEAST_BACKTEST_UNIVERSE`, `_START`, `_END`, `_REPORTS`) are applied before anything is derived from
them - see [docs/RUNNING.md](docs/RUNNING.md).

`reconcile.py` is the one tool that still looks for a `Backtest_Core` checkout, it is optional, and it
**adopts** that checkout's universe and window rather than assuming the two configs agree - because
they will not. `UNIVERSE` went `midcap150` -> `midcap150_ddq` -> `smallcap250` in a single session
while this folder was being built, and the comparison was refused three times for exactly that reason.

---

## What a run writes

`qbeast-backtest` prints the result table **and** writes every report into
`reports/<universe>/`:

| file | what it is |
|---|---|
| `dashboard.html` | the fund dashboard — cumulative return, drawdown vs the index, idle cash, gross-vs-after-tax, regime map, index candles with all six trend gates raced, calendar-year and monthly returns, the rebalance table, the signal grid, executed trades, config and audit |
| `equity.csv` | the daily curve: NAV, pre-tax NAV, invested, cash, deployment %, drawdown %, benchmark |
| `summary.json` | the headline numbers for this universe, strategy-relative and benchmark-relative, plus the friction split — what the scenario matrix reads |
| `tradebook_backtest.csv` | every fill: date, symbol, side, qty, price, charges, slippage, notional |
| `tradebook_by_symbol_backtest.csv` | the same fills per name |
| `orderbook_backtest.csv` | every order and its status — **Nautilus engine only** |
| `book_by_stock_backtest.csv`, `book_summary_backtest.txt` | per-name P&L and the one-page summary — **Nautilus only** |
| `nautilus_nav.csv` | the canonical Nautilus NAV — **Nautilus only** |
| `staircase/` | `flagged.csv`, `rebalance.csv`, `blocked.csv`, `episodes.csv`, `run.json` |

```bash
qbeast-backtest                       # everything
qbeast-backtest --no-dashboard        # skip dashboard.html, the slow part
qbeast-backtest --no-report           # print the table only
```

**The order book is Nautilus-only, the trade book is not.** `qbeast_exec.export_books` reads
`engine.trader`, so no order object exists on the internal ledger — only fills. `report.py` writes the
trade book from `ledger.trades` instead, so `--engine internal` is still auditable fill by fill.

**The dashboard.** `src/qbeast_backtest/dashboard.py` renders the fund dashboard. It labels every
chart from the running strategy's `name` and `params`, so it describes whatever you register rather
than anything that came before. `stats` comes from `report.stats` — the shared library's
`performance_stats` plus the short key aliases the plotting code was written against (same values,
verified to 1e-9).

The panels that charted the removed reference strategy's own signals — an index overlay of its gate
levels, and a tab racing its gate variants — return empty and their tabs are dropped. To chart your
own signal, publish it in `StrategyOutput.diagnostics`.

---

## Both engines reconcile

| engine | result |
|---|---|
| `internal` | **PASS** - Rs 0.0000, 0 of 2,498 sessions differ |
| `nautilus` | **PASS** - Rs 0.0000, 0 of 2,498 sessions differ |

Three causes had to be found, and each one was invisible to the other arm:

1. **The slippage model was built from windowed frames.** The two engines consume
   `liquidity_slippage()` *differently*: the internal ledger calls `.rate(sym, date, qty, price)` and
   uses the size-aware MODEL, while Nautilus gets `NautilusFeeModel(cfg, extra_rate=dict(slippage))`
   which takes the **flat dict values** and discards the model. Windowing changed the per-symbol rate
   on **217 of 217** symbols (mean 9.4 bps) - so the internal arm stayed exact to the rupee while the
   Nautilus arm drifted **+5.09%** over ten years. Both now build it from full history, as the
   monolith does.
2. **The breaker was handed a reindexed trend series.** It evaluates `trend_up.asof(d)` daily,
   including on dates the signal index has but the trading calendar does not, so the calendar-reindexed
   reporting copy changed its re-entry decisions. Strategies now publish the raw series through
   `StrategyOutput.execution_inputs`.
3. **`CHARGES_MODE` was never pinned** - Core on `blended`, this copy on `itemised`, worth 0.1% of
   terminal NAV. `reconcile.py` now pins **every shared scalar setting** rather than a curated list;
   the curated list had already leaked `WEIGHTING`, then `DD_*`, then this.

## What is NOT changed

`Backtest_Core` is untouched, and **nothing here links to it**. This package carries its own config,
its own copy of the certified maths, its own `qbeast_exec`, and its own `reports/`, and the two run
independently. The only point of contact is `reconcile.py`, which is optional and read-only.

`qbeast_exec` is **copied, not forked**: it is the shared team library and the copy here should be
kept in step with the one every other strategy reads. If you change it, change it there and re-copy —
a per-strategy fork of what a trade costs defeats the entire purpose of this split.
