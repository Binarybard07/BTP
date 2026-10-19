# Architecture — why there are four execution-related files, and what each one is for

```
stage1_data  ──MarketData──▶  stage2_strategy  ──StrategyOutput──▶  stage3_execution  ──▶ ExecutionResult
   prices, calendar,            THE ONLY PART A               the T→T+1 loop,
   survivorship,                NEW STRATEGY CHANGES          costs, tax, engine choice
   staircase → ELIGIBLE SET                                         │
                                                                    ▼
                                                        qbeast_exec (shared library)
```

Each boundary is a frozen dataclass with a `validate()` — see [`contracts.py`](../src/qbeast_backtest/contracts.py).
A fault is reported where it was introduced, not three stages later as an implausible number.

---

## The question: can Nautilus not do what `engine.py` does?

**Mostly it can — but not the two things that matter most for an Indian delivery-equity book.**
Verified against the installed **nautilus_trader 1.231.0**, not against an old migration note.

### What Nautilus does, and does better

| capability | Nautilus |
|---|---|
| orders, order lifecycle, next-bar fills | ✅ a real matching engine |
| commissions | ✅ `FeeModel`, `calculate_commission` |
| slippage | ✅ `FillModel` — but **probabilistic**, which is why `RegimeFeeModel` is custom: we need a deterministic per-stock bps |
| positions, realised / unrealised PnL | ✅ `realized_pnl` across 8 modules |
| cash account, no borrowing | ✅ `CashAccount`, `allow_cash_borrowing=False` |

### What it does not do

**1. Indian FIFO capital-gains tax — no concept of it at all.**

```
files mentioning capital gains / STCG / LTCG / tax_lot / cost_basis :  0
files mentioning FIFO / LIFO lot accounting                         :  0
```

`realized_pnl` is **not** a taxable gain. Tax needs per-lot cost basis and per-lot holding period:
FIFO matching, the STCG/LTCG split by **sale date** at 23-Jul-2024, the ₹1.25 L LTCG exemption
(₹1.00 L pre-FY2024), financial-year assessment, and s.74 loss carry-forward. None of that is
expressible in position-level PnL.

**2. Idle-cash yield — `CashAccount` has no accrual.**

Its full method list contains no `interest`, `accrue`, `yield` or `tax`. The only `interest_rate` in
the package sits in an options cost-of-carry struct (`underlying_price`, `cost_of_carry`, `vol`,
`pnl`) — Black-Scholes inputs, not a balance earning 4.5 %.

### So `engine.py` earns its place three ways

1. **Speed.** Milliseconds against 60–90 s per Nautilus call. That difference is what makes
   multi-universe sweeps and the weighting matrix possible at all.
2. **An independent control.** Two implementations agreeing is evidence; one implementation is an
   assertion. `reconcile.py` trades on exactly that.
3. **It carries the tax + yield layer** Nautilus lacks.

### And note: Nautilus does not do tax here either

`nautilus_run.replay_fills()` takes Nautilus's fills and replays them through `engine.Ledger` →
`qbeast_exec.TaxEngine`. Tax and cash yield are a **post-hoc overlay on both engines**, from one
shared implementation. The docstring records why: an earlier version hand-rolled tax there and became
*"a FOURTH copy of the law, and the least accurate one"* — applying today's rates across the whole
history and over-taxing ~17 of 19 years.

> **Known cost of that design.** Because tax is applied *after* Nautilus has sized and traded,
> **Nautilus sizes off pre-tax equity** — roughly **3 pp of unfunded leverage and 485 negative-cash
> days**, overstating CAGR by about 0.8 pp. Fixing it properly means tax becoming an *in-loop* account
> effect, which Nautilus cannot give natively. The internal ledger does not have this problem: it
> deducts tax from cash as it goes.

---

## The four files, side by side

| file | lines | role | depends on Nautilus? |
|---|---|---|---|
| `engine.py` | 351 | **The internal ledger.** Integer shares, FIFO tax, cash yield, next-open fills, charges and slippage hitting cash. The fast path and the control. | no |
| `nautilus_backtest.py` | 119 | **Nautilus foundation.** Venue, INR instruments, `RegimeFeeModel`, `build_engine()`. | yes |
| `nautilus_run.py` | ~540 | **Nautilus driver.** Bars into the engine, the `RegimeExec` strategy that turns targets into orders, and `replay_fills` / `replay_fills_pretax` — the tax + cash-yield overlay. | yes |
| `qbeast_exec/nautilus.py` | 181 | **The reusable harness.** `make_bars`, `make_equity`, `DeferredSubmit`, and the T→T+1 contract. | yes |

### They used to overlap. They no longer do.

`nautilus_run` had its **own** `make_bars` and `nautilus_backtest` its own `make_equity` — second
copies of the T→T+1 bar builder, functionally identical with nothing keeping them that way. Both now
**delegate to `qbeast_exec`**, verified to produce byte-identical instruments and bars.

Two things that surfaced while doing it, both worth knowing:

- **The venues did not agree.** `nautilus_backtest` has used `Venue("XNSE")` since the migration;
  `qbeast_exec` defaults to `"NSE"`. Delegating naively would have renamed every instrument id in
  every order book and fill export — silently, and only visible in files nobody diffs. `XNSE` is what
  the reconciliation validates, so it stays, and it is now **passed explicitly** rather than inherited
  from either default.
- **`nautilus_run` still imported the monolith.** `from backtest import load_frames, rebalance_dates`
  — a module this package does not contain, so the Nautilus path was broken outright. The internal
  reconciliation passed anyway, because the internal path never imports that module. *A green suite
  said nothing about it.* Those helpers are data and calendar concerns and now live in
  `stage1_data.loader`.

---

## The T→T+1 contract, and why it has one implementation

A daily strategy decides on day `t`'s close and can only trade at `t+1`'s open. Under bar execution
that breaks in two ways that look identical in the output:

1. **Wrong day** — an order submitted on `t`'s bar fills on `t`. Same-bar look-ahead.
2. **Wrong price** — an order submitted before the session's own bar is processed matches against the
   newest bar the engine *has*, which is the previous session's. Right day, stale price.

The second is the nastier one: invisible in a date audit, affecting ~99.9 % of fills, and in a momentum
book **systematically favourable** because it buys names that gapped up. Measured on one real run
before it was fixed: **+0.25 % mean discount per fill, worth Rs 84,728 of return that did not exist.**

Three pieces enforce it, all in `qbeast_exec.nautilus`:

- `make_bars(..., offset_ns=0)` for the **clock** instrument, `offset_ns=1` for every tradable — same
  timestamp, one nanosecond apart, so the clock bar is always processed first.
- `DeferredSubmit` — holds an order until the next session.
- `replay_fills(opens=...)` — reprices fills to the fill-day open.

That is why it gets exactly one implementation, in the shared library, read by the backtest, the paper
book and any live adapter.

---

## Where the shared concerns live

| concern | module | notes |
|---|---|---|
| charges (STT, exchange, SEBI, stamp, GST, DP) | `qbeast_exec.ChargesCalculator` | itemised NSE delivery schedule |
| slippage | `qbeast_exec.SlippageModel` | Corwin-Schultz spread + √size impact |
| capital-gains tax | `qbeast_exec.TaxEngine` | FIFO lots, 23-Jul-2024 split, s.74 carry-forward |
| circuit locks | `qbeast_exec.PriceBandModel` | Tier-1 band gate |
| operator ladders | `qbeast_exec.StaircaseFlag` | **applied in stage 1**, backstopped in stage 3 |
| point-in-time membership | `qbeast_exec.survivorship` | **applied in stage 1** — it decides what may be *selected* |
| performance statistics | `qbeast_exec.performance_stats` | CAGR on calendar years, plus `ann_arith` and `simple` |

Survivorship is listed under stage 1 deliberately. The *code* is in the shared library, but whether a
stock was in the index on a date determines **what may be selected**, so it has to gate the pool before
ranking — the same place as the staircase.
