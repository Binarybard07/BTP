# Technical reference

File by file, function by function, with the code quoted in place. For whoever has to change this.

**This bundle ships the framework and no strategy.** Stages 1 and 3, the contracts, costs, slippage,
tax and both execution engines are here and certified. Stage 2 is an empty slot you fill. If you are
only trying to get a first run out, start with [HANDOFF.md](HANDOFF.md) instead.

**How to read the code blocks.** Every block is copied from the tree, with two allowances: `...` marks
an elided body or argument list, and a trailing `#` comment may be this document's rather than the
source's. Nothing else is paraphrased.

A block preceded by `<!-- example -->` is the exception: it is **illustrative code that is not in this
bundle** — which is unavoidable here, since the thing being illustrated is the strategy you have not
written yet. The audit skips those and checks every other line.

`audit_reference.py` checks that claim, plus every file name, line count, function name, dependency
bound and console script here, against the tree:

```bash
python audit_reference.py
```

Line counts are as of 19-Oct-2026 and drift as the code changes — treat a mismatch as a stale
document, not a missing file.

---

## 0. Technology

| | where | why this |
|---|---|---|
| **Python ≥ 3.10** | `pyproject.toml` | `X \| Y` unions, frozen dataclasses throughout |
| **pandas ≥ 2.0 / numpy ≥ 1.24** | everywhere | the panel model is DataFrame-shaped: dates × symbols |
| **scipy ≥ 1.10** | `portfolio.py` | `scipy.optimize.minimize` (SLSQP) for min-variance and max-Sharpe |
| **plotly ≥ 5.0** | `dashboard.py` | plotly's `get_plotlyjs()` is inlined into the page, so `dashboard.html` opens with no network |
| **NautilusTrader ≥ 1.190** *(extra)* | `nautilus_run.py` | event-driven execution; **optional** — the internal ledger needs none and is the fast path |

`patsy` is **not** a dependency here. It was required by the removed reference strategy's
penalised-spline trend gates; nothing in this bundle imports it.

```toml
[project.scripts]
qbeast-backtest          = "qbeast_backtest.pipeline:main"
qbeast-backtest-selftest = "qbeast_backtest.selftest:main"
qbeast-backtest-reconcile= "qbeast_backtest.reconcile:main"
qbeast-exec-selftest     = "qbeast_exec.selftest:main"
```

**Two top-level packages from one distribution.** `qbeast_backtest` is the backtest; `qbeast_exec` is
the shared execution library (costs, tax, tradability, engines). The split is what stops a strategy
change altering what a trade costs — and it is why you can read §6 once and never think about it
again.

---

## 1. The shape of a run

```
pipeline.run()
   │
   ├─ stage1_data.load_market_data()      ──►  MarketData       prices, calendar, ELIGIBLE SET
   │       loader.py  tradability.py  staircase_log.py
   │
   ├─ stage2_strategy.get(name).decide()  ──►  StrategyOutput   ◄── YOUR CODE, and only this
   │       registry.py  base.py
   │
   └─ stage3_execution.execute()          ──►  ExecutionResult  fills, costs, tax, NAV
           runner.py  →  qbeast_exec (Ledger | Nautilus)
                      │
                      └─ report.write()   ──►  dashboard.html, equity.csv, books, summary.json
```

Each arrow is a **frozen validated dataclass**, checked before the next stage runs — so a fault is
reported where it was introduced, not three stages later as an implausible number.

---

## 2. `contracts.py` — the wiring (252 lines)

The only file every stage imports. Read this one properly; everything else follows from it.

### `MarketData` — everything your strategy is allowed to see

```python
@dataclass(frozen=True)
class MarketData:
    calendar: pd.DatetimeIndex          # the sessions this run trades on (from the BENCHMARK)
    opens: pd.DataFrame                 # fill prices: what a market-on-open order gets at T+1
    closes: pd.DataFrame                # decision prices ON THE CALENDAR, forward-filled
    volumes: pd.DataFrame               # for liquidity / slippage / the staircase
    benchmark: pd.Series                # the measurement series AND the calendar's source
    signal_index: pd.Series             # what the trend gate and vol regime read (may differ)

    eligible: Mapping[pd.Timestamp, frozenset]
    rebalance_dates: Sequence[pd.Timestamp]
    last_session_fillable: bool = False

    history_closes: pd.DataFrame = field(default_factory=pd.DataFrame)
    history_volumes: pd.DataFrame = field(default_factory=pd.DataFrame)
    warmup_bars: int = 400
```

Three fields carry lessons that cost real money:

**`eligible[t]`** is the set you MAY hold on session `t` — point-in-time membership, minus
tradability-flagged names, minus anything unpriced. `StrategyOutput.validate()` **rejects** anything
outside it. That is what makes survivorship control and the tradability gate *binding* rather than
advisory, and it is the check most likely to fail your first run.

**`history_closes` vs `closes`.** `closes` is the trading window; `history_closes` runs from the start
of the data. Compute trailing signals on the latter:

> The first version of this contract shipped only the window. A 12-1 momentum signal had nothing to
> warm up on, the first fully-ranked session slipped **371 days**, and the run finished at **Rs 1.04M
> against the correct Rs 3.12M**. Nothing errored.

**`last_session_fillable`.** A decision on `calendar[-1]` cannot be filled in a *backtest* — there is
no next open in the data. Live, it is the *only* decision that matters. Use
`StrategyBase.decision_sessions(md)`; hand-rolling `if t == md.calendar[-1]: continue` is right for a
backtest and silently produces a live book that never places an order.

### `StrategyOutput` — what you return

```python
targets: Mapping[pd.Timestamp, Mapping[str, float]]
exposure: pd.Series | None = None        # intended deployment, 0..1 of NAV
posture: pd.Series | None = None        # a label: "cash" / "sized" / "full"
diagnostics: pd.DataFrame | None = None  # anything else worth plotting, REINDEXED to the calendar
execution_inputs: Mapping[str, object] = field(default_factory=dict)
```

`targets[t] = {}` means **go to cash**. A *missing* key means *not a decision session, hold*.
Conflating them is how a flatten instruction gets read as "hold" — fixed once already in the Nautilus
adapter.

`execution_inputs` exists because `diagnostics` is reindexed and forward-filled — right for plots,
wrong for anything read with `.asof()`. The drawdown breaker reads `trend_up.asof(d)` *daily*,
including dates the signal index has but the trading calendar does not. **Measured: handing it the
reindexed frame moved the Nautilus arm 2.44% at the end while the internal arm stayed exact to the
rupee.**

`validate()` enforces four rules — weights ≤ `CASH_BUFFER`, no negative weights, targets on-calendar,
and nothing outside `eligible[t]`.

---

## 3. Stage 1 — data and tradability

Shared. You do not normally touch any of it; if you need an input it does not provide, add it **here**
so every strategy gets it on one convention, not privately in your own module.

### 3.1 `data_io.py` (122 lines) — the only file that reads disk

```python
def load_ohlcv(symbol: str, directory=None) -> pd.DataFrame:
def load_index() -> pd.DataFrame:
def load_signal_index() -> pd.DataFrame:        # may be a DIFFERENT index from the benchmark
def universe() -> list[str]:
def trading_window(df: pd.DataFrame) -> pd.DataFrame:
```

One CSV per symbol: `date,open,high,low,close,volume`, dates `DD-MM-YYYY` (`config.DATE_FORMAT`).
The index CSV's **filename must equal `INDEX_SYMBOL`** — `universes.py` validates it, because getting
it wrong once wrote MidCap prices into a file named "NIFTY 100" and corrupted both the benchmark *and*
the trend gate that read it.

### 3.2 `stage1_data/loader.py` (186 lines) — builds `MarketData`

```python
closes_ff = closes.reindex(cal).ffill()
...
return MarketData(
    calendar=cal, opens=opens.reindex(cal), closes=closes_ff, volumes=volumes.reindex(cal),
    history_closes=closes, history_volumes=volumes,
    warmup_bars=int(getattr(C, "WARMUP_BARS", 400)),
    benchmark=index_close.reindex(cal).ffill(),
    signal_index=sig_close,                      # NOT reindexed: the gate reads it with .asof()
    ...)
```

The asymmetry is deliberate: `closes` is forward-filled onto the calendar, `signal_index` is not.

### 3.3 `stage1_data/tradability.py` (160 lines) — who may be traded

```python
class TradabilityGate:
    def flagged_on(self, t: pd.Timestamp) -> frozenset:
    def eligible_on(self, t: pd.Timestamp, row: pd.Series | None = None) -> frozenset:
    def build(self, calendar: pd.DatetimeIndex, closes_ff: pd.DataFrame, ...):
```

`eligible_on` is the intersection: **membership → priced → minus `flagged_on(t)`**. The gate runs at
*selection* time, so a removed name's slot goes to the next genuine candidate and the book still holds
`TOP_N`. Filtering after your weights would leave the book deliberately under-invested with the
flagged name's weight sitting in cash.

### 3.4 `stage1_data/staircase_log.py` (186 lines) — the audit trail

Writes `reports/<universe>/staircase/`: `run.json`, `flagged.csv`, `rebalance.csv` (with a
**`mattered`** column — did removing the name actually change the book?), `blocked.csv`,
`episodes.csv`.

---

## 4. Stage 2 — your slot

**This is the only part of the system you write.** Two files decide what runs.

### 4.1 `stage2_strategy/registry.py` (45 lines) — ships empty, and refuses

```python
REGISTRY: dict = {}

DEFAULT = None


def get(name: str | None = None, **kwargs):
    key = str(name or DEFAULT or "").strip().lower()
    if not REGISTRY:
        raise KeyError(
            "no strategies are registered. This build ships the FRAMEWORK only - stages 1 and 3, the "
            "contracts, costs, slippage, tax and both execution engines - and no strategy. Write one "
            ...)
```

A registry rather than dynamic discovery: a strategy that appears in a run because a file happened to
be on disk is a strategy nobody chose. An empty registry **refuses and explains**; a typo raises with
the registered names rather than quietly running a default and reporting it under the name you asked
for.

To register yours:

```python
from .my_strategy import MyStrategy

REGISTRY = {MyStrategy.name: MyStrategy}
DEFAULT = MyStrategy.name          # optional - lets you omit --strategy
```

### 4.2 `stage2_strategy/base.py` (76 lines) — the two helpers you should use

```python
@staticmethod
def eligible_on(md: MarketData, t: pd.Timestamp) -> frozenset:
    s = md.eligible.get(t)
    if s is None:
        raise KeyError(...)        # and NOT `md.eligible.get(t, set(md.closes.columns))`
    return frozenset(s)

@staticmethod
def decision_sessions(md: MarketData) -> list:
    last = md.calendar[-1]
    return [t for t in md.rebalance_dates if t != last or bool(md.last_session_fillable)]
```

The default for a missing eligible set must be **empty and loud**, not permissive and quiet —
defaulting to "the whole universe" silently restores every name the gate just removed.

`self_check(md, out)` runs the contract validation and raises. Call it in development: failing there
is cheap, failing at the stage boundary after a 90-second Nautilus run is not.

### 4.3 The shape of a strategy

<!-- example -->
```python
class MyStrategy(StrategyBase):
    name = "my_strategy"

    def decide(self, md: MarketData) -> StrategyOutput:
        from qbeast_backtest import config as C

        sig = md.history_closes.pct_change().rolling(60, min_periods=40).std()   # FULL history
        sig_c = sig.reindex(md.calendar).ffill()                                 # read on calendar

        targets, expo = {}, {}
        for t in self.decision_sessions(md):
            elig = self.eligible_on(md, t)
            row = sig_c.loc[t].dropna()
            pick = list(row[[s for s in row.index if s in elig]].nsmallest(int(C.TOP_N)).index)
            targets[t] = {s: float(C.CASH_BUFFER) / len(pick) for s in pick} if pick else {}
            expo[t] = 1.0 if pick else 0.0

        return StrategyOutput(targets=targets, exposure=pd.Series(expo).sort_index(),
                              name=self.name, params=dict(top_n=int(C.TOP_N)))
```

`params` is what the dashboard prints and `summary.json` records, so a stored result always carries
the settings that produced it. Three optional `diagnostics` columns light up extra dashboard panels —
see §7.2.

**The reference strategy is not in this bundle.** `signals.py`, `strategy.py`, `regime_qbeast.py` and
`stage2_strategy/regime_switch.py` were removed before handoff. Whatever signal, posture rule or
regime model you need is yours to write — the framework has no opinion about it beyond the contract.

---

## 5. What the framework still gives you

### 5.1 `portfolio.py` (343 lines) — four weighting schemes

`equal` | `invvol` | `minvar` | `sharpe_lw`, selected by `config.WEIGHTING`. Risk estimated causally:

```python
def _returns(closes: pd.DataFrame, symbols, date, lookback: int) -> pd.DataFrame:
    """Slicing by label and then tailing is deliberate: it can only ever see rows at or before `date`,
    so no reordering or reindexing accident upstream can leak a future bar in."""
    px = closes.loc[:pd.Timestamp(date), cols].tail(int(lookback) + 1)
    return px.pct_change().iloc[1:]
```

A name with fewer than `min_obs` returns takes 1/N rather than being dropped — **dropping it would
change WHICH stocks are held, and a weighting choice must never do that.** `minvar` and `max_sharpe`
use SLSQP with bounded weights; `_covariance(shrink=True)` is Ledoit-Wolf.

Use it from your strategy if you want more than equal weight:

<!-- example -->
```python
from qbeast_backtest import portfolio
w = portfolio.allocate(scheme, closes, pick, date, budget, lookback=..., min_obs=...)
```

### 5.2 `engine.py` (351 lines) — slippage and the internal ledger

```python
class SlippageRates(dict):
    """A {symbol: rate} mapping that ALSO carries the size-aware model."""
    def rate(self, symbol: str, date=None, qty: float = 0.0, price: float = 0.0) -> float:
```

That dual nature caused a bug worth knowing about:

> The internal ledger calls `.rate(...)` — in dynamic mode that evaluates the size-aware **model** and
> the dict values are never read. Nautilus gets `NautilusFeeModel(cfg, extra_rate=dict(slippage))` —
> which takes the **flat dict values** and discards the model. Windowing the input frames therefore
> changed the per-symbol rate on 217 of 217 symbols (mean 9.4 bps): the internal arm stayed exact to
> the rupee while the Nautilus arm drifted **+5.09%** over ten years.

Hence `runner.py` builds it from full history: `liquidity_slippage(md.history_closes, md.history_volumes)`.

### 5.3 `breaker.py` (119 lines) — the drawdown breaker

`DrawdownBreaker` / `from_config()`, driven by `DD_EXIT` / `DD_REENTRY` / `DD_RECOVER`. Honoured by
**both** engines. It reads `trend_up` daily via `.asof()`, which is why `execution_inputs` exists.

---

## 6. Stage 3 — execution, costs and tax

Shared and certified. **Do not reimplement any of it in your strategy.**

### 6.1 `stage3_execution/runner.py` (228 lines)

```python
def execute(md: MarketData, out: StrategyOutput, *, engine: str | None = None,
            bands=None, stair=None, verbose: bool = True) -> ExecutionResult:
    eng = str(engine or C.EXEC_ENGINE).lower()
    slip = liquidity_slippage(md.history_closes, md.history_volumes)
    if eng == "nautilus":
        led, led_pre, fills = _nautilus(md, out, bands=bands, stair=stair, slip=slip)
    else:
        led     = _internal(md, out, tax=True,  bands=bands, stair=stair, slip=slip)
        led_pre = _internal(md, out, tax=False, bands=bands, stair=stair, slip=slip)
```

Two ledgers on the internal path — post-tax and pre-tax — because the dashboard shows both and one
cannot be re-derived from the other once FIFO lots are involved.

`_nautilus` feeds the engine **your** decisions; it does not recompute them:

```python
NR._TARGETS = {pd.Timestamp(t): dict(w) for t, w in out.targets.items()}
NR._SIG_CLOSE = md.signal_index
NR._TREND_UP  = (out.execution_inputs or {}).get("trend_up")
```

### 6.2 `qbeast_exec` — the shared library (15 modules)

| module | lines | what |
|---|---|---|
| `tax.py` | 333 | `TaxEngine`, FIFO `Lot`/`LotClose`, STCG/LTCG, FY assessment, exemption, `gross_up` |
| `charges.py` | 491 | `ChargesCalculator` — STT, exchange, SEBI, stamp, GST, DP; `NautilusFeeModel` |
| `ledger.py` | 249 | config-free `Ledger`, `apply_fill`, `replay_fills(opens=)`, `aftertax_nav` |
| `nautilus.py` | 181 | `make_bars`, `make_equity`, `DeferredSubmit` — the T→T+1 contract |
| `books.py` | 238 | `export_books` — order book, trade book, per-stock roll-up |
| `bands.py` | 675 | `PriceBandModel`, `MarketWideBreaker`, the `StaircaseFlag` front door |
| `circuit_flags.py` | 1644 | **QBEAST circuit flags v2.4.1**, vendored verbatim |
| `staircase241.py` | 431 | the adapter: wide frames → v2.4.1's long frame → `TRADE_BLOCKED` |
| `survivorship.py` | 292 | point-in-time `Membership`, `assert_no_look_ahead` |
| `universe.py` | 170 | `UniverseReport` — per-universe `summary.json` + cross-universe table |
| `qbeast_exec/report.py` | 403 | `performance_stats`, `Dashboard`, `drawdown_table`, `make_equity`. Not `qbeast_backtest/report.py` (327), which writes this project's report set |
| `isolation_audit.py` | 312 | proves the library has no reach-back into any project |
| `gen_signatures.py` | 162 | regenerates `API_SIGNATURES.md` |
| `qbeast_exec/selftest.py` | 788 | 206 arithmetic checks |
| `qbeast_exec/__init__.py` | 87 | the public surface |

**`qbeast_exec/dashboard.py` is absent from this bundle** — it carried the removed strategy's
trend-gate vocabulary. `Dashboard`, `performance_stats`, `make_equity` and `drawdown_table` live in
`qbeast_exec/report.py` and are unaffected. This copy is therefore a **subset of the canonical library
at the same version string**, so it cannot be synced from upstream with a plain file copy.

**Tax is an overlay, not an engine feature.** Nautilus has no capital-gains tax, no FIFO lots and no
idle-cash yield, so those are applied by the ledger afterwards — which is why `_equity()` sizes off
`min(overlay NAV, broker cash + holdings)`.

### 6.3 The tradability gate — circuit flags v2.4.1

`circuit_flags.py` is the library (stdlib + pandas only); `staircase241.py` adapts it:

```python
circ  = CF.compute_circuit_hit_flag(df, mode="eod")
stair = CF.compute_staircase_flag(circ, p)
full  = CF.compute_chronic_illiquidity_flag(stair, c)
gated = CF.build_universe_exclusion_table(full, None, mode="eod")
out   = CF.four_flag_columns(gated, mode="eod")      # -> TRADE_BLOCKED
```

Anything but `"NO"` means **no buy, no sell, no exit** — the position is still marked to market. The
lookup is `.asof` on purpose:

```python
def gate(self, symbol: str, date) -> str:
    """A name that was blocked and then stopped printing has not become tradable."""
```

Full rules, the measured cost and the retired config knobs: [STAIRCASE_v2.4.1.md](STAIRCASE_v2.4.1.md).

### 6.4 `nautilus_run.py` (500 lines) — the cash budget

```python
orders.sort(key=lambda x: x[1])      # SELLS (negative) first -> free cash before buys
```

Sound only while every sell fills. v2.4.1 refuses sells on a tagged name; submitting the buys anyway
overdrew the account, and a Nautilus **CASH** account answers that by *aborting the run*. Now:

```python
_ALLOW = 1.0 + 3.0 * float(getattr(C, "COST_PCT", 0.0015))
budget = self.portfolio.account(VENUE).balance_free(INR).as_double()
...
    if delta > 0 and px > 0:
        need = abs(delta) * px * _ALLOW
        if need > budget:
            _BLOCKED.append(dict(..., reason="unfunded"))
            continue
        budget -= need
```

**If you write orders yourself anywhere, consult the gate on every order** — not once at selection.

---

## 7. Reporting

### 7.1 `report.py` (327 lines)

```python
def write(r: dict, outdir=None, *, dashboard: bool = True, verbose: bool = True) -> dict:
```

Writes `equity.csv`, `summary.json` (via `UniverseReport`), the trade book, `nautilus_nav.csv`, and
calls the dashboard. Each file's failure is **isolated** — losing the dashboard to a plotting error
must not also cost the equity curve.

`stats()` is `qbeast_exec.performance_stats` plus short key aliases (`maxdd`=`max_dd`, `yrs`=`years`,
…) the plotting code was written against — same values, verified to 1e-9, aliased in one place rather
than renamed in forty.

`_dash_res()` adapts the contract to the dashboard's flat dict and derives the rebalance log from
`targets` itself, so there is no second place for the two to disagree.

### 7.2 `dashboard.py` (757 lines) — 13 tabs

One self-contained HTML file (~5 MB, plotly.js inlined). Every chart labels itself from your
strategy's `name` and `params`.

Overview · Returns (tax) · Regime map · Index · Year by year · Monthly returns · Rebalance ·
Signal grid · Trend prediction · Feature selection · Trades · Configuration · Logs

Three optional `diagnostics` columns light up panels, and are omitted if you do not publish them:

| publish | and you get |
|---|---|
| `posture` (or `StrategyOutput.posture`) — `full`/`sized`/`cash` | **Regime map** shaded by posture over your deployment |
| `level` — whatever curve you gate, rank or size on | your line overlaid on the benchmark candles in **Index** |
| `mom` — your selection signal | the **Signal grid**, the universe ranked by it |

That is the generic replacement for the removed strategy's own overlays, which drew its trend gate on
the candles and its regime bands on the map.

---

## 8. Configuration

`config.py` (468 lines) is the single source. `universes.py` (359) makes a universe **one word**:

```python
UNIVERSE = "nifty100"      # SOURCE, INDEX_SYMBOL, MEMBERSHIP_FILE and the overrides move together
```

Data root is one environment variable:

```python
DATA_ROOT = Path(os.environ.get("QBEAST_DATA_ROOT") or r"E:\Final_Data_Shared_22SEP2026")
```

Two settings from the removed strategy's block are **not** tuning and stayed:

```python
TOP_N = _ov("top_n", 20)       # how many names the book holds. Universe-wide, not strategy-specific:
CASH_BUFFER = 0.97             # the deployment CEILING. `StrategyOutput.validate()` refuses weights
```

`CASH_BUFFER` is an **enforced framework rule**, not a preference — removing it would not relax the
rule, it would break the check. Everything that *was* tuning (trend gate, moving-average length,
p-spline window and penalty, volatility-regime split, vol-target ladder) went with the strategy.

`config.py` refuses to import on a look-ahead violation: `BACKTEST_START` before the universe's first
reconstitution raises unless `REGIMESWITCH_ALLOW_LOOKAHEAD=1`.

---

## 9. Verification

```bash
qbeast-backtest-selftest       # 44 contract/boundary checks
qbeast-exec-selftest           # 206 cost/tax/slippage/gate checks
```

Run both **before** writing anything. If they pass, the shared layers are intact and any later failure
is yours — which is the point of running them first.

`reconcile.py` (254 lines) compares against an upstream checkout when one is present and skips cleanly
when it is not; it is a development tool and will not work for you without that checkout.

---

## 10. Things that will bite you

1. **Compute trailing signals on `history_closes`**, not `closes`. Cost of getting it wrong: Rs 1.04M vs Rs 3.12M, silently.
2. **Loop over `decision_sessions(md)`**, not `md.rebalance_dates`.
3. **`{}` ≠ missing key** in `targets` — flatten versus hold.
4. **Most of `config` is bound at import.** Read it inside `decide()`, not at module level, if you want a runtime override to take effect.
5. **`SlippageRates` is read two different ways** by the two engines. Build it from full history.
6. **Nautilus has no tax, no FIFO lots, no cash yield.** They are overlays, which is why sizing uses `min()`.
7. **`reports/` is per universe.** Switching universes never overwrites the last one's numbers.
8. **`qbeast_exec` here is a subset** — no `dashboard.py`. Do not sync it from upstream with a plain file copy; a blanket copy of `__init__.py` alone breaks every import in the bundle.
9. **Read your own `summary.json`.** `survivorship` must be `true` if you meant it, and `deployment` in `equity.csv` tells you whether a flat NAV means "held nothing" or "held things that did not move". A strategy that looks excellent while 90% in cash has not been tested on much.
