# Writing a strategy

A strategy is a class with a `name` and a `decide(MarketData) -> StrategyOutput`. That is the entire
interface. It reads what stage 1 gives it and returns what it wants to hold. It does **not** load data,
place orders, compute costs, or decide what is tradeable — those are shared, and they have to stay
shared or no two strategies can be compared.

---

## 1. Write it

`src/qbeast_backtest/stage2_strategy/my_strategy.py`

```python
from qbeast_backtest.contracts import MarketData, StrategyOutput
from .base import StrategyBase


class MyStrategy(StrategyBase):
    name = "my_strategy"

    def decide(self, md: MarketData) -> StrategyOutput:
        import pandas as pd
        from qbeast_backtest import signals          # the certified signal library

        # TRAILING SIGNALS GO ON md.history_closes.  See §4 - this one is not negotiable.
        mom = signals.momentum(md.history_closes).reindex(md.calendar).ffill()

        targets, expo = {}, {}
        for t in md.rebalance_dates:
            if t == md.calendar[-1]:
                continue                              # no next open to fill at
            elig = self.eligible_on(md, t)            # survivorship + staircase already applied
            r = mom.loc[t].reindex(sorted(elig)).dropna()
            if r.empty:
                targets[t] = {}                       # {} means FLATTEN - see §3
                continue
            picks = r.nlargest(20).index
            w = 0.97 / len(picks)                     # 0.97 = config.CASH_BUFFER
            targets[t] = {s: w for s in picks}
            expo[t] = 0.97

        return StrategyOutput(targets=targets, exposure=pd.Series(expo).sort_index(),
                              name=self.name, params=dict(top_n=20))
```

## 2. Register it

`src/qbeast_backtest/stage2_strategy/registry.py`

```python
from .my_strategy import MyStrategy

REGISTRY = {
    MyStrategy.name: MyStrategy,
    MyStrategy.name: MyStrategy,          # <- add this
}
```

A registry rather than directory scanning: a strategy that appears in a run because a file happened to
be on disk is a strategy nobody chose.

## 3. Run it

```bash
qbeast-backtest --strategy my_strategy --engine internal
```

Nothing in stage 1 or stage 3 changes. Same prices, same eligibility rules, same staircase gate, same
charges, slippage, tax and execution engine as every other strategy — so any difference in the result
is the strategy.

---

## The rules, all enforced at the boundary

### Read only what `MarketData` gives you

If you need an input it does not have, **add it to stage 1** — where every strategy gets it on one
convention — not privately in stage 2. The selftest asserts that a strategy imports neither
`stage1_data` nor `stage3_execution` and never touches `data_io` or `read_csv`; hold your own strategy
to the same line.

### `{}` is not the same as a missing key

```python
targets[t] = {}      # FLATTEN: sell everything, go to cash
# no key for t       # not a decision session: hold
```

Conflating those is how a flatten instruction gets read as "hold". This project has fixed that exact
bug once already, in the Nautilus adapter.

### Trailing signals go on `md.history_closes`, never `md.closes`

`closes` is the **trading window**. `history_closes` runs from the start of the data.

> The first version of this contract shipped only the window. 12-1 momentum then had nothing to warm up
> on: the first fully-ranked session slipped **371 days** (2016-09-30 → 2017-10-06), the book held
> almost nothing for its first year, and the run finished at **Rs 1.04 M against the monolith's
> Rs 3.12 M**. Nothing errored — the strategy simply had no signal and dutifully held nothing.

`MarketData.validate()` now refuses a run with less than `WARMUP_BARS` (400) of history behind the
first traded session.

### Rank inside `eligible_on(md, t)` — do not widen it

```python
elig = self.eligible_on(md, t)                       # correct
elig = md.eligible.get(t, set(md.closes.columns))    # WRONG
```

A missing key means stage 1 published no set for that session. Defaulting to "the whole universe"
silently restores every name the staircase and the membership filter just removed. `eligible_on` raises
instead, and `StrategyOutput.validate()` fails any target outside the published set — which is what
makes those gates binding rather than advisory.

---

## What the contract will reject

| `StrategyOutput.validate()` fails if | because |
|---|---|
| weights sum above `CASH_BUFFER` | that is leverage nobody authorised |
| any weight is negative | this framework is long-only |
| a target is dated off the trading calendar | it could never execute |
| **any name is outside the eligible set** | survivorship or the staircase gate was bypassed |

Run `qbeast-backtest-selftest` after touching a boundary — 27 checks, and they exist specifically to
catch a *misbehaving strategy*, which neither the reconciliation nor the arithmetic suites can.

---

## Checklist before you trust a new strategy's number

1. `qbeast-backtest-selftest` — the contracts still bind.
2. `qbeast-exec-selftest` — the cost and tax arithmetic is intact (211 checks).
3. `qbeast-backtest --engine internal` — it runs, and read the stage-2 line: how many sessions held a
   book, how many flattened to cash. A strategy that flattens 90 % of the time is in cash, not clever.
4. `qbeast-backtest --engine nautilus` — the source of record. If the two engines disagree materially,
   that is a finding, not a rounding difference.
5. Read `reports/<universe>/staircase/rebalance.csv` — the `mattered` column tells you whether the
   tradability gate actually changed your book.
