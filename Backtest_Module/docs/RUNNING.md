# Running it

## Install

```bash
cd Backtest_Modular
pip install -e .                 # the framework + the shared library
pip install -e ".[nautilus]"     # ... and the source-of-record engine
```

Two top-level packages are installed:

| package | what it is |
|---|---|
| `qbeast_backtest` | the framework: contracts, pipeline, the three stages, the certified maths |
| `qbeast_exec` | **the shared team library**: charges, slippage, tax, price bands, the staircase gate, survivorship, the Nautilus harness, reporting |

`qbeast_exec` is top-level on purpose: a paper book or a live adapter can `import qbeast_exec` knowing
nothing about this framework. One implementation of what a trade costs and what tax it owes, read by
everything.

**NautilusTrader is an optional extra.** The internal ledger runs without it, and that is the fast path
every research sweep uses — milliseconds against 60–90 s per Nautilus call.

---

## The four commands

```bash
qbeast-backtest                      # the full run: stage 1 → 2 → 3, then the result table
qbeast-backtest-selftest             # 27 contract-conformance checks
qbeast-exec-selftest                 # 211 arithmetic checks (charges, slippage, tax, bands)
qbeast-backtest-reconcile            # compare against a Backtest_Core checkout (optional)
```

Each also works as a module, which is handy in a venv without the scripts on `PATH`:

```bash
python -m qbeast_backtest.pipeline --engine internal
python -m qbeast_backtest.selftest
python -m qbeast_exec.selftest
python -m qbeast_backtest.reconcile --engine internal
```

### `qbeast-backtest` flags

| flag | effect |
|---|---|
| `--strategy NAME` | pick a registered strategy. **Required** — this build ships none; register yours in `stage2_strategy/registry.py` |
| `--list` | print the registered strategies |
| `--engine internal\|nautilus` | override `config.EXEC_ENGINE` for this call |
| `--no-staircase` | turn the tradability gate off — for an A/B of its effect |

---

## A full run, start to finish

```bash
pip install -e ".[nautilus]"
qbeast-exec-selftest                      # 211 passed  - the arithmetic is intact
qbeast-backtest-selftest                  # 27 passed   - the contracts bind
qbeast-backtest --engine internal         # fast: read the three stage banners
qbeast-backtest --engine nautilus         # the source of record
```

Then read, in this order:

1. The **stage-2 line** — how many sessions held a book and how many flattened to cash. A strategy
   that flattens 90 % of the time is in cash, not clever.
2. The **stage-3 line** — the staircase backstop should refuse **0** buys. Non-zero means something
   reached execution without going through stage 1.
3. `reports/<universe>/staircase/rebalance.csv` — the **`mattered`** column: did the gate change the
   book, or merely fire?
4. The **result table** — CAGR (compounded, calendar years), annualised arithmetic, and the
   uncompounded figure, each against the benchmark.

---

## Configuration

`src/qbeast_backtest/config.py` is **this package's own** and the only place its settings live. It
began as a copy of `Backtest_Core/config.py` and is now independent: nothing pulls from that folder,
and the package runs with `Backtest_Core` absent or deleted.

### Environment overrides

Applied **before** anything is derived from them — `DATA_DIR`, `OUT_DIR`, the membership file and the
benchmark are all derived from `UNIVERSE` at import, so these cannot be set afterwards.

| variable | effect |
|---|---|
| `QBEAST_BACKTEST_UNIVERSE` | e.g. `smallcap250` |
| `QBEAST_BACKTEST_START` | e.g. `2016-04-29` |
| `QBEAST_BACKTEST_END` | e.g. `2026-05-29` |
| `QBEAST_BACKTEST_REPORTS` | where outputs go |
| `QBEAST_BACKTEST_CORE` | a `Backtest_Core` checkout for `reconcile` |

```bash
QBEAST_BACKTEST_UNIVERSE=nifty100 QBEAST_BACKTEST_START=2019-01-01 qbeast-backtest
```

### Where outputs go

`REPORTS_ROOT` resolves in this order, first answer wins:

1. `$QBEAST_BACKTEST_REPORTS` — explicit, and what a scheduled run should set
2. `<project root>/reports` — when running from a source checkout, the normal case
3. `./reports` — relative to the working directory

An installed package therefore never writes inside `site-packages`.

---

## `reconcile` — the acceptance test

```bash
qbeast-backtest-reconcile --engine internal     # must stay Rs 0.00
qbeast-backtest-reconcile --engine nautilus     # the source-of-record engine
qbeast-backtest-reconcile --effect              # what the staircase change does
```

Two arms, answering two different questions:

| arm | staircase pool-exclusion | expectation |
|---|---|---|
| default | **OFF** | arm B must equal arm A **to the rupee**. This isolates the *refactor* — any difference is a bug introduced while moving code. |
| `--effect` | **ON** | arm B is *expected* to differ. This isolates the *change* — the number is the finding, never a pass condition. |

Conflating those is how a refactor bug gets filed as a strategy improvement.

**Current status:** `--engine internal` → **Rs 0.0000 max difference, 0 of 2,498 sessions differ.**

### If it reports a configuration mismatch

That is the guard working. The two configs are independent now and `Backtest_Core/config.py` is edited
constantly — `UNIVERSE` went `midcap150` → `midcap150_ddq` → `smallcap250` in a single session. So arm
B **adopts** Core's universe and window through the env overrides, and the guard compares the *calendar
and the data folder*, not just the scalar settings:

> Two arms can agree on every setting and still read different data. That is exactly how a 76 % NAV gap
> once got reported as a refactor failure, when the arms were running different universes over
> different windows.

If it still refuses, align the two configs or point `$QBEAST_BACKTEST_CORE` at the checkout you mean.

`Backtest_Core` is **optional**. Without it, `reconcile` says so and exits 0; nothing else needs it.

---

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

## Troubleshooting

| symptom | cause |
|---|---|
| `only N session(s) of history before the window opens` | `BACKTEST_START` is too early for the data. Trailing signals need `WARMUP_BARS` (400) behind the first traded session. |
| `no eligibility set for <date>` | stage 1 published none for a rebalance session. Do **not** substitute the full universe — that undoes the staircase gate and survivorship. |
| `N name(s) outside the eligible set` | the strategy ranked outside what stage 1 allowed. Use `self.eligible_on(md, t)`. |
| `weights sum to X, above the cash buffer` | unauthorised leverage; weights must sum to ≤ `CASH_BUFFER`. |
| staircase backstop refused > 0 buys | something reached execution without the stage-1 filter. |
| `ModuleNotFoundError: nautilus_trader` | `pip install -e ".[nautilus]"`, or use `--engine internal`. |
