# Tradability gate: circuit flags v2.4.1 — read before you trust an old number

**19-Oct-2026.** The staircase / circuit / illiquidity gate has been replaced with **QBEAST circuit
flags v2.4.1**. The library is vendored at `src/qbeast_exec/circuit_flags.py` and reaches your code
through `qbeast_exec.StaircaseFlag`, whose interface is unchanged.

**If you ran this framework before 19-Oct-2026, your numbers will move.** That is expected, and the
size of it as measured on the source project's own universes is below.

---

## What the rules are now

| | old flag | v2.4.1 |
|---|---|---|
| direction | the pump only (`direction="up"`) | **both** — CLIMBING *and* FALLING |
| what is blocked | **buys only** (`block_sell=False`) | **no buy, no sell, no exit** |
| marking | — | position is still **marked to market** while blocked |
| untagging | tag lapsed when the trailing window moved on | **E normal sessions** — not locked, not single-price, volume ≥ `normal_vol_frac` × the clean baseline |
| also gated | — | **chronic illiquidity** (hysteresis) and the **market-wide breaker** |

The gate column is `TRADE_BLOCKED`. Anything other than `"NO"` means no trade in either direction.

### Why sells are blocked, since this is the change people argue with

The old config shipped `block_sell=False` with the comment that "a name in a staircase must still be
EXITABLE — blocking sells would trap the book in exactly the position it should be getting out of".
That is reasoning about the book, not about the market: a ladder locked at its band **has no bid**, so
the exit it was protecting does not exist. Allowing it made every backtest cheaper than reality.

---

## What it cost, measured

These were measured in the source project with a four-arm harness (`staircase_ab.py`), which runs the
same universe and window with the gate off, under the old rules, under v2.4.1, and under v2.4.1 with
only the trading rule relaxed — so the detector change and the trading-rule change can be told apart.
That harness is not in this bundle because it needs a strategy to run; the numbers are reported here
so you know what the gate does before you see it in your own results.

**nifty100_ddq, 2019-01-01 → 2026-05-29, internal engine:**

| arm | CAGR | maxDD |
|---|---|---|
| gate off | 14.27 | −16.26 |
| **old flag** | **14.27** | **−16.26** |
| v2.4.1, full rule | 13.25 | −16.32 |
| v2.4.1, sells/exits open | 12.70 | −16.24 |

**The old flag and "no gate at all" are identical to the rupee.** On this universe the old gate found
nothing, so any run you have labelled *staircase ON* was in fact **ungated**. Decomposition: the
detector is −1.57pp, the trading rule **+0.55pp** — blocking the exit *helped* here, because it stopped
the book selling into a locked market. The sign is not knowable in advance and the harness reports it
either way.

**nifty100_ddq, full history 2003 → 2026, Nautilus + sharpe_lw:** 16.92% → **13.34%** CAGR, drawdown
unchanged at −22.1%. Excess over the index +5.64pp → **+2.06pp**. So on a large-cap universe the gate
costs return and buys no extra protection — worth deciding deliberately rather than inheriting.

---

## It also broke the Nautilus engine, and that is fixed

`nautilus_run._submit()` sorts sells first and funds the buys from proceeds that have not arrived
(`# SELLS (negative) first -> free cash before buys`). v2.4.1 refuses a funding sell, the loop
submitted the buys anyway, and a Nautilus CASH account answers an overdraft by **aborting the run**:

```
2021-07-02 [ERROR] BacktestEngine: Stopping backtest from AccountBalanceNegative(balance=-25313.78)
```

Not a flaw in the gate — an assumption the old flag never violated, because it only refused buys.
Fixed with a cash budget: settled cash, plus the proceeds of sells **actually submitted**, minus each
buy as it is placed. A buy that no longer fits is refused as `reason="unfunded"` and carried to the
next rebalance.

**If you have a run that logged `AccountBalanceNegative`, discard it.** It was not merely truncated —
up to the abort it was buying with money the account did not have.

---

## What you have to change

**Nothing, if you only consume `StaircaseFlag`.** `.check()`, `.flagged_symbols()`, `.blocked_table()`
and `.summary()` all still work.

**Your config, if you set the old knobs.** These are **refused by name**, not ignored — a parameter
that is accepted and then does nothing is how a risk control quietly stops working:

| gone | use instead |
|---|---|
| `STAIRCASE_WINDOW`, `STAIRCASE_MIN_HITS` | — there is no trailing window; a tag needs *S consecutive* ladder days |
| `STAIRCASE_MIN_RUN` | `STAIRCASE_START_DAYS` (S, default 5) |
| `STAIRCASE_BLOCK_SELL` | — the rule is both sides; see below for a deliberate A/B |
| `direction`, `require_lock` | — both directions always; day classes come from the library |

New: `STAIRCASE_START_DAYS` (5), `STAIRCASE_END_DAYS` (2), `STAIRCASE_NORMAL_VOL` (0.5),
`STAIRCASE_VOL_LOOKBACK` (20), `STAIRCASE_CHRONIC` (True). `STAIRCASE_MIN_PRICE` is unchanged — it is
about the tick grid, not the detector.

**If you write orders yourself**, consult the gate on **every** order, not just at selection. The paper
books did it once for BUY at selection time, so trims and exits went through untouched.

---

## Commands

```bash
qbeast-exec-selftest                   # 206 checks, incl. the two-direction rule
qbeast-backtest-selftest               # 44 contract checks
```

To measure the rule's cost without trading against it:

```python
StaircaseConfig(..., block_sell=False, block_exit=False, _allow_relaxed=True)
```

`summary()` then reports `RELAXED`, so a half-off run can never be mistaken for a full one.

`StaircaseFlagV1` / `StaircaseConfigV1` are retained for one purpose: reproducing numbers that were
produced with the old rules. Do not use them for new work.

---

## Verification

The vendored library passes its own suites unmodified — **32/32** install checks and **27/27** unit
tests, including v2.4.1's new `test_both_staircase_directions_block_buy_and_sell`. Project side:
**206** `qbeast_exec` and **44** contract checks in this bundle.

## Open question

`STAIRCASE_CHRONIC = True` gates chronic illiquidity, which is a *separate* reason from a staircase. On
a large-cap universe it may be excluding names that trade perfectly well at this book's size — that is
the most likely source of the 3.58pp on nifty100_ddq. Set `STAIRCASE_CHRONIC = False` in
`config.py` to see your own result with the staircase alone.
