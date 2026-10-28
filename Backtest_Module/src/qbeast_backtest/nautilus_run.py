"""
qbeast_backtest/nautilus_run.py
----------------------------
NautilusTrader migration — STAGES 2 & 3.

  STAGE 2: load daily bars from data/clean into a Nautilus BacktestEngine.
  STAGE 3: a Nautilus Strategy that EXECUTES stage 2's targets as real orders.

Design: the *signals* stay in the validated code (signals.py + strategy.py) — we precompute the exact
same monthly target weights the current backtest uses, and Nautilus's only job is EXECUTION (turn targets
into orders, fill next bar, track cash/positions). That keeps the strategy logic identical for stage-5
reconciliation. Charges here are the stage-1 placeholder fee; the faithful COST_PCT + per-stock slippage
FeeModel and the custom tax / cash-yield layer are STAGE 4.

Run:  python nautilus_run.py [START] [END]      (default 2023-01-01 .. 2025-12-31, a fast slice)
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from nautilus_trader.model.currencies import INR
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import AggregationSource, BarAggregation, OrderSide, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

# These live in stage 1 now - they are data and calendar concerns, not execution ones.
from qbeast_backtest.stage1_data.loader import load_frames, rebalance_dates
from qbeast_backtest.config import COST_PCT, INITIAL_CAPITAL, OUT_DIR, POINT_IN_TIME
from qbeast_backtest.engine import liquidity_slippage
from qbeast_backtest.membership import Membership
from qbeast_backtest.nautilus_backtest import VENUE, RegimeFeeModel, build_engine, index_instrument_symbol, make_equity
from qbeast_exec.nautilus import make_bars
from qbeast_backtest.nautilus_books import export_books
from qbeast_backtest import config as C
from qbeast_backtest import slippage_model as SM

_DAY = BarSpecification(1, BarAggregation.DAY, PriceType.LAST)
_TARGETS: dict = {}          # date -> {SYM: weight}
_REBAL_DATES: set = set()    # set of python date objects
_INDEX_ID = None             # the index instrument id (used as the daily rebalance clock)
_FILLS: list = []            # captured Nautilus fills -> fed to the stage-4b after-tax overlay
_ORDER_DATE: dict = {}       # client_order_id -> DECISION date (day t); fill is t+1. For the order book.
_BANDS = None                # optional qbeast_exec PriceBandModel -> TIER-1 circuit gate (live/paper).
                             # Enforced at SUBMISSION: Nautilus falls back to normal fill logic when a
                             # FillModel returns an empty book, so a lock cannot be applied at fill time.
_BLOCKED: list = []          # orders refused by the circuit gate
_STAIR = None               # qbeast_exec.StaircaseFlag -> refuse BUYs in an operator ladder
_INITIAL = None              # capital the mirror ledger starts from (defaults to config.INITIAL_CAPITAL)
_MIRROR = None               # engine.Ledger shadowing the tax + cash-yield overlay -> see _equity()
_OVERLAY_SIZING = True       # False restores the legacy PRE-TAX sizing (kept only for A/B comparison)
_BREAKER = None              # breaker.DrawdownBreaker for THIS run (config.DD_EXIT)
_SIG_CLOSE = None            # signal-index closes, for the breaker's 'recover' re-entry
_TREND_UP = None             # trend-gate series, for the breaker's 'gate' re-entry


def precompute(*_a, **_k):
    """REMOVED WITH THE STRATEGY, and it is worth knowing what it was.

    This recomputed the removed strategy's signals, posture and target weights, then handed them to
    the Nautilus strategy - a SECOND implementation of the decision, beside the real one. The stage pipeline never called it: `stage3_execution` sets `_TARGETS` from
    the `StrategyOutput` stage 2 produced, which is the whole point of the split. It existed only for
    the legacy standalone `run_backtest()` entry point.

    So `--engine nautilus` is unaffected by its removal: it executes whatever YOUR stage 2 decided.
    """
    raise NotImplementedError(
        "precompute() held the removed reference strategy, and the pipeline does not use it: "
        "stage3_execution sets nautilus_run._TARGETS from your StrategyOutput. Run "
        "`qbeast-backtest --strategy <yours> --engine nautilus`.")


class RegimeExec(Strategy):
    """Execution only: on the index bar of each rebalance date, drive positions to the precomputed targets."""

    def on_start(self):
        global _MIRROR, _BREAKER
        self._last = {}
        self._done = set()
        self._pending = None                                     # target decided on day t, submitted at t+1
        self._marked = set()                                     # dates the mirror has already accrued
        from qbeast_backtest.engine import Ledger
        # THE MIRROR. Nautilus's account models neither capital-gains tax nor the cash yield -- both are
        # applied afterwards by replay_fills(). So its own equity is a PRE-TAX number that drifts above
        # the NAV actually reported (measured: +8.6% by 2026 on smallcap250), and sizing from it deploys
        # money the reported NAV has already paid to the tax office. This Ledger is fed the same fills
        # and marked on the same calendar, so _equity() can size from the NAV that will be REPORTED.
        _MIRROR = Ledger(float(_INITIAL if _INITIAL is not None else INITIAL_CAPITAL), tax=True)
        from qbeast_backtest import breaker as _brk
        _BREAKER = _brk.from_config()                             # config.DD_EXIT, same class as dd_sweep
        if _BREAKER.enabled and (_SIG_CLOSE is None or _TREND_UP is None):
            raise RuntimeError(
                "config.DD_EXIT is set but this Nautilus entry point never called precompute(), so the "
                "breaker has no signal series for its re-entry rule. Refusing rather than trading a "
                "breaker that can halt but never resume.")
        for inst in self.cache.instruments():
            self.subscribe_bars(BarType(inst.id, _DAY, AggregationSource.EXTERNAL))

    def on_order_filled(self, event):
        q = float(event.last_qty)
        sym = event.instrument_id.symbol.value
        px = float(event.last_px)
        comm = float(event.commission.as_double()) if event.commission is not None else 0.0
        signed = q if event.order_side == OrderSide.BUY else -q
        _FILLS.append(dict(ts=int(event.ts_event), symbol=sym, qty=signed, px=px, comm=comm))
        if _MIRROR is not None:                                  # keep the mirror on the same book
            apply_fill(_MIRROR, pd.Timestamp(event.ts_event).normalize(), sym, int(signed), px, comm)

    def _prices(self) -> dict:
        """symbol -> last close seen. The newest price available at SUBMISSION time, which for a stock
        is the PREVIOUS session's close (the index bar is fed before the day's stock bars)."""
        return {iid.symbol.value: v for iid, v in self._last.items()}

    def _equity(self) -> float:
        """The equity target weights are sized against: the LESSER of what the overlay says this book
        owns and what the broker can actually fund.

        The two disagree by exactly (interest - tax), and BOTH directions are a real failure:

          tax > interest  -- the broker figure is PRE-TAX and drifts above the NAV that gets reported
                             (measured +8.6% by 2026). Sizing off it deploys money already paid to the
                             tax office: >100% deployed, cash negative on 19% of days.
          interest > tax  -- the MIRROR figure includes a cash yield the Nautilus account never receives
                             (Ledger.mark accrues it; the broker does not). Sizing off it orders stock
                             the broker cannot pay for, and a CASH account answers that with
                             AccountBalanceNegative, which ABORTS the whole backtest and leaves a
                             truncated book behind.

        min() is the only choice that is safe in both: it never deploys past after-tax wealth, and never
        past settled cash. The cost is under-deploying by |interest - tax| when interest leads, which is
        bounded and small (<1.5% of equity in the early years of a run)."""
        acct = self.portfolio.account(VENUE)
        cash = acct.balance_total(INR).as_double()
        held = sum(float(p.quantity) * self._last.get(p.instrument_id, 0.0)
                   for p in self.cache.positions_open())
        broker = cash + held
        if _OVERLAY_SIZING and _MIRROR is not None:
            return min(float(_MIRROR._nav_at(self._prices())), broker)
        return broker

    def on_bar(self, bar):
        iid = bar.bar_type.instrument_id
        self._last[iid] = float(bar.close)
        if iid != _INDEX_ID:
            return
        # tell the dynamic-slippage FillModel which day it is filling (index bar precedes the stock bars,
        # and orders submitted here fill on THIS date's stock open)
        SM.set_current_date(pd.Timestamp(bar.ts_event))
        # Accrue the mirror EXACTLY once per trading day (Ledger.mark applies one day of cash yield and
        # the FY tax assessment). The index bar is the one per-day synchronisation point the strategy has.
        if _MIRROR is not None:
            d = pd.Timestamp(bar.ts_event).normalize()
            if d not in self._marked:
                self._marked.add(d)
                _MIRROR.mark(d, self._prices())
        # The index (clock) bar is fed BEFORE the day's stock bars, so orders submitted on it fill at
        # TODAY's stock OPEN. We DECIDE on a rebalance day t but DEFER the orders to the NEXT index bar, so
        # they fill at t+1's open -- matching engine.py's next-day-open rule (no same-bar look-ahead).
        if self._pending is not None:                            # submit the prior rebalance's target -> t+1 open
            self._submit(*self._pending)
            self._pending = None
        day = pd.Timestamp(bar.ts_event).date()
        # PORTFOLIO DRAWDOWN BREAKER (config.DD_EXIT), on the mirror's NAV -- the same NAV the reports
        # show, and the same breaker.DrawdownBreaker the dd_sweep research runs. NAV is as of the
        # previous close, which is all a market-on-open order could have known anyway.
        if _BREAKER is not None and _BREAKER.enabled and _MIRROR is not None:
            d = pd.Timestamp(bar.ts_event).normalize()
            nav = _MIRROR.nav_hist.get(d, float("nan"))
            px = float(_SIG_CLOSE.asof(d)) if _SIG_CLOSE is not None else float(bar.close)
            up = bool(_TREND_UP.asof(d)) if _TREND_UP is not None else True
            if _BREAKER.step(d, nav, px, up) == "HALT":
                self._done.add(day)                              # a halt session never also rebalances
                self._pending = (day, {})                        # flatten the book at the next open
                return
            if _BREAKER.halted:
                self._done.add(day)
                return                                           # stay in cash until re-entry fires
        if day in _REBAL_DATES and day not in self._done:
            self._done.add(day)
            tw = next((w for d, w in _TARGETS.items() if d.date() == day), None)
            if tw is not None:
                self._pending = (day, tw)                        # (decision date t, target); fills next bar (t+1)

    def _submit(self, decision_date, tw):
        eq = self._equity()
        want = {}
        for sym, wt in tw.items():
            sid = InstrumentId(Symbol(sym), VENUE)
            px = self._last.get(sid)
            if px and px > 0:
                want[sid] = int(wt * eq / px)
        for p in self.cache.positions_open():                    # flatten anything no longer targeted
            want.setdefault(p.instrument_id, 0)
        orders = [(sid, tgt - int(self.portfolio.net_position(sid))) for sid, tgt in want.items()]
        orders = [(sid, d) for sid, d in orders if d != 0]
        orders.sort(key=lambda x: x[1])                          # SELLS (negative) first -> free cash before buys

        # A BUY MAY ONLY SPEND CASH THAT WILL ACTUALLY BE THERE.
        # The sort above funds the buys from proceeds that have not arrived yet, which is sound only
        # while every sell fills. Circuit flags v2.4.1 refuses sells and exits on a tagged name, so a
        # funding sell can now be blocked - and submitting the buys anyway overdraws the account. A
        # CASH account answers that with AccountBalanceNegative and ABORTS the run, which is what
        # happened on 2021-07-02 (balance -25,313.78) and left a book truncated at that date.
        #
        # So: start from settled cash, add the proceeds of the sells actually SUBMITTED, and subtract
        # each buy as it goes in. A buy that no longer fits is refused and carried to the next
        # rebalance - the same thing `continue` means for every other block in this loop.
        #
        # The allowance covers charges and slippage on both sides, so the budget is not spent down to
        # the last rupee and then overdrawn by the fee. It is deliberately crude: being slightly
        # under-invested is a measurable cost, while an abort destroys the run.
        # TWO allowances, because they cover different unknowns:
        #   COST  charges + slippage, which is what COST_PCT is for
        #   GAP   the close-to-open move a market-on-open order cannot know. Measured on smallcap250
        #         (397,051 gaps): mean |gap| 1.01%, buy q75 +0.83%, sell q10 -1.06%. The previous
        #         single allowance was 3*COST_PCT = 0.45% - under half the mean - so the budget was
        #         systematically optimistic on BOTH legs and a routine gap overdrew the account.
        # Applied in the ADVERSE direction on each leg: buys cost more, sells raise less.
        _COST = 3.0 * float(getattr(C, "COST_PCT", 0.0015))
        _GAP = float(getattr(C, "NAUTILUS_GAP_ALLOW", 0.01))
        _ALLOW = 1.0 + _COST + _GAP
        budget = self.portfolio.account(VENUE).balance_free(INR).as_double()
        _unfunded = 0

        for sid, delta in orders:
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            sym = sid.symbol.value
            fill_date = SM.current_date() or decision_date
            sd = "BUY" if delta > 0 else "SELL"
            if _BANDS is not None:                               # circuit-locked? the order cannot fill
                if not _BANDS.check(sym, fill_date, sd, abs(delta)):
                    _BLOCKED.append(dict(date=fill_date, symbol=sym, side=sd, qty=abs(delta),
                                         reason="circuit"))
                    continue                                     # carried to the next rebalance
            if _STAIR is not None:                               # tagged ladder? v2.4.1 blocks BOTH sides
                if not _STAIR.check(sym, fill_date, sd, abs(delta)):
                    _BLOCKED.append(dict(date=fill_date, symbol=sym, side=sd, qty=abs(delta),
                                         reason="staircase"))
                    continue
            px = float(self._last.get(sid, 0.0) or 0.0)
            if delta > 0 and px > 0:
                need = abs(delta) * px * _ALLOW
                if need > budget:
                    _BLOCKED.append(dict(date=fill_date, symbol=sym, side=sd, qty=abs(delta),
                                         reason="unfunded"))
                    _unfunded += 1
                    continue
                budget -= need
            order = self.order_factory.market(sid, side, Quantity.from_int(abs(delta)))
            _ORDER_DATE[order.client_order_id.value] = str(decision_date)   # order PLACED on day t (fills t+1)
            self.submit_order(order)
            if delta < 0 and px > 0:
                # Proceeds net of the SAME adverse allowance: a sell whose open gaps down raises
                # less than this estimate, so the budget must not assume the close.
                budget += abs(delta) * px * max(0.0, 1.0 - _COST - _GAP)

        if _unfunded:
            self.log.info(f"{decision_date}: {_unfunded} buy(s) refused as unfunded - the cash a "
                          f"blocked sell would have raised did not arrive")


_SPLIT_WARNED = False


def split_commission(sym: str, q: int, px: float, comm: float) -> tuple[float, float]:
    """Recover (charges, slippage) from the single commission figure Nautilus reports.

    The fee model builds it as  calc.total(side, qty, px) + qty*px*extra_rate[symbol]  -- statutory
    charges plus the cost-based slippage add-on. Re-running the FIRST term through the same shared
    ChargesCalculator therefore recovers the split EXACTLY; it is arithmetic, not an estimate.

    Why it matters: every Nautilus report previously logged slippage as 0.00 and folded it into
    commission, so a reader could not see what execution cost versus what the exchange charged -- on
    smallcap250 that hid over Rs 1.1M of slippage inside a "charges" column.

    Under `--slip-dyn` the fee model charges COST_PCT only, because slippage moves the FILL PRICE
    instead. The slippage returned here is then correctly ~0: it is in the price, not the commission,
    and `_slippage_report` measures it there."""
    global _SPLIT_WARNED
    from qbeast_backtest.engine import _charges
    charges = float(_charges().total("BUY" if q > 0 else "SELL", abs(int(q)), float(px)))
    slip = float(comm) - charges
    if slip < -0.01:
        # Negative means the replay's ChargesCalculator disagrees with the one the engine ran, i.e. the
        # charge schedule changed between execution and replay. Attributing that to slippage would be
        # fiction, so report it once and fall back to charging it all as charges.
        if not _SPLIT_WARNED:
            _SPLIT_WARNED = True
            print(f"  [fees] WARNING: recomputed charges exceed the commission Nautilus reported "
                  f"({sym}: charges {charges:,.2f} > commission {comm:,.2f}). The charge schedule "
                  f"differs between execution and replay, so the charges/slippage split is not "
                  f"reliable for this run; the TOTAL is unaffected.", flush=True)
        return float(comm), 0.0
    return charges, max(slip, 0.0)


def apply_fill(led, t, sym: str, q: int, px: float, comm: float) -> None:
    """Apply ONE fill to a Ledger: cash, FIFO tax lots, position, trade record.

    ONE implementation, used by both `replay_fills` (which builds the reported Nautilus ledger) and the
    in-strategy mirror that makes sizing overlay-aware. They must never drift: the whole point of the
    mirror is that the equity the strategy SIZES from is the equity the replay REPORTS."""
    led.cash -= comm                                  # Nautilus commission already carries the slippage
    led.cost_paid += comm                             # add-on from the shared fee model
    gs = gl = 0.0
    if q > 0:
        led.cash -= q * px
        led.tax.buy(sym, t, q, px)
        led.pos[sym] = led.pos.get(sym, 0) + q
    else:
        led.cash += (-q) * px
        g = led.tax.sell(sym, t, -q, px)
        gs, gl = g["short"], g["long"]
        led.pos[sym] = led.pos.get(sym, 0) + q
        if led.pos[sym] == 0:
            led.pos.pop(sym, None)
    # ATTRIBUTION ONLY -- `cost` stays the full commission, so cash, NAV and every P&L number are
    # untouched. This only stops the reports showing slippage as zero when it was really charged.
    _charge, _slip = split_commission(sym, q, px, comm)
    led.trades.append(dict(date=t, symbol=sym, qty=q, px=px, cost=comm, commission=_charge,
                           slippage=_slip, realized_short=gs, realized_long=gl))


def replay_fills(fills, closes, cal, initial, tax: bool = True, opens=None):
    """STAGE 4b — replay Nautilus's fills through the SHARED tax + cash-yield layer (the two things
    Nautilus does not model), returning the resulting Ledger.

    WHY A LEDGER AND NOT JUST A NAV: this is the Nautilus SOURCE OF RECORD, so the reports (daily log,
    holding period, FY tax statement, dashboard) must be able to read positions, lots, realised gains
    and tax events off it exactly as they read the internal ledger.

    WHAT THIS FIXES: the previous version hand-rolled the tax here — a FOURTH copy of the law, and the
    least accurate one. It applied today's rates (STCG 20% / LTCG 12.5% / Rs1.25L exemption) across the
    WHOLE history, ignoring the 23-Jul-2024 regime change and the Rs1.00L pre-FY2024 exemption, so it
    over-taxed ~17 of the 19 years. Going through engine.Ledger means the Nautilus NAV is taxed by
    qbeast_exec.TaxEngine — the same rules, split by sale date and FY, as everything else."""
    from qbeast_backtest.engine import Ledger

    led = Ledger(float(initial), tax=tax)
    by_day: dict = {}
    for f in fills:
        by_day.setdefault(pd.Timestamp(f["ts"]).date(), []).append(f)
    closes_ff = closes.reindex(cal).ffill()          # value open positions on the trading calendar
    opens_c = opens.reindex(cal) if opens is not None else None
    repriced = moved = 0
    for t in cal:
        # sells (negative qty) before buys, so sale proceeds fund the purchases and cash never dips
        # negative intraday — the same ordering the internal ledger uses.
        for f in sorted(by_day.get(t.date(), []), key=lambda x: x["qty"]):
            px = float(f["px"])
            if opens_c is not None:
                # PRICE AT THE FILL DAY'S OPEN. Nautilus prices this order against the newest stock bar
                # it has processed, which is the PREVIOUS session's -- the day's own bar arrives after
                # the index bar the order was submitted on. Filling at a price from a session that has
                # already closed is not executable, and in a momentum book it is systematically
                # favourable (it buys names that gapped up). Nautilus still decides WHICH orders fill
                # and WHEN; only the price is corrected, to the one the internal ledger already uses.
                o = float(opens_c.loc[t].get(f["symbol"], float("nan"))) if t in opens_c.index else float("nan")
                if np.isfinite(o) and o > 0 and abs(o - px) > 1e-9:
                    moved += abs(o - px) * abs(int(f["qty"]))
                    repriced += 1
                    px = o
            apply_fill(led, t, f["symbol"], int(f["qty"]), px, float(f["comm"]))
        led.mark(t, closes_ff.loc[t])                 # applies the cash yield + the FY tax assessment
    if opens_c is None and fills:
        print("  [fills] WARNING: no opens panel passed to replay_fills, so fills keep the price "
              "Nautilus assigned -- which is the PREVIOUS session's close, not the fill-day open. "
              "Pass opens= to price them correctly.", flush=True)
    elif repriced:
        print(f"  [fills] repriced {repriced:,} of {sum(len(v) for v in by_day.values()):,} fills to "
              f"the fill-day open (Rs {moved:,.0f} of notional moved); Nautilus had priced them at the "
              f"previous session's close.", flush=True)
    return led


def replay_fills_pretax(fills, closes, cal, initial, opens=None):
    """The same replay with the capital-gains deduction switched OFF -- the GROSS curve. Identical fills,
    so the ONLY difference from `replay_fills` is the tax, which is what "pre-tax" is supposed to mean."""
    return replay_fills(fills, closes, cal, initial, tax=False, opens=opens)


def aftertax_nav(fills, closes, cal, initial, opens=None):
    """The Nautilus after-tax daily NAV (see `replay_fills` for the ledger behind it)."""
    return pd.Series(replay_fills(fills, closes, cal, initial, opens=opens).nav_hist).sort_index()


def execute_targets(targets, cal, opens, closes, index_close, slip, initial, label=None, bands=None,
                    volumes=None):
    """Execute ALREADY-DECIDED target weights on NautilusTrader; return (after-tax ledger, pre-tax ledger).

    The single place the Nautilus engine is wired up, shared by the research backtest and the paper /
    forward path. It takes `targets` instead of recomputing them, which matters for the paper path: those
    targets come from the LIVE-overlaid feed, and recomputing here would silently re-derive them from the
    clean data and gate the paper book on the wrong prices.

    `slip` is the per-symbol rate map, added on top of the shared charge schedule by the fee model.
    `label` (e.g. "paper") names the exported order/trade books; None skips the export."""
    global _TARGETS, _REBAL_DATES, _FILLS, _ORDER_DATE, _INDEX_ID, _BANDS, _INITIAL
    from qbeast_backtest.nautilus_backtest import RegimeFeeModel, build_engine, index_instrument_symbol, make_equity

    syms = sorted(set().union(*[set(v) for v in targets.values()])) if targets else []
    if not syms:
        raise RuntimeError("execute_targets: every target is empty - nothing to execute.")
    _TARGETS, _REBAL_DATES, _FILLS, _ORDER_DATE = targets, {d.date() for d in targets}, [], {}
    _BANDS = bands
    _INITIAL = float(initial)          # the mirror must start from THIS book's capital

    engine = build_engine(int(initial), fee_model=RegimeFeeModel(None, dict(slip or {})))
    idx = make_equity(index_instrument_symbol()); _INDEX_ID = idx.id
    engine.add_instrument(idx)
    ic = index_close.reindex(cal)
    engine.add_data(make_bars(idx, ic, ic, ic, ic, None, offset_ns=0))
    for s in syms:
        if s not in closes.columns:
            continue
        inst = make_equity(s); engine.add_instrument(inst)
        # REAL volume matters: make_bars() falls back to volume=1 when it is None, and a 1-unit bar makes
        # Nautilus fill 1 share at the open and the remainder at a worse price -- turning every order into
        # a partial fill at a distorted price. Pass the actual traded volume.
        v = volumes[s].reindex(cal) if (volumes is not None and s in volumes) else None
        engine.add_data(make_bars(inst, opens[s].reindex(cal) if s in opens else None, None, None,
                                  closes[s].reindex(cal), v, offset_ns=1))      # stocks AFTER the index bar
    engine.add_strategy(RegimeExec(config=StrategyConfig()))
    engine.run()
    if label:
        from qbeast_backtest.nautilus_books import export_books
        export_books(engine, label, OUT_DIR, order_dates=_ORDER_DATE, opens=opens)
    fills = list(_FILLS)
    engine.dispose()
    return (replay_fills(fills, closes, cal, initial, opens=opens),
            replay_fills_pretax(fills, closes, cal, initial, opens=opens))


def _slippage_report(calib, fills, opens, cal) -> None:
    """Realised slippage of every fill vs the ACTUAL open it was benchmarked against: the realised adverse
    rate (the emergent p), the mean drag (should track mu) and the two-sidedness."""
    opens_ff = opens.reindex(cal).ffill()
    rows = []
    for f in fills:
        t = pd.Timestamp(f["ts"]).normalize()
        s = f["symbol"]
        if s not in opens_ff.columns:
            continue
        idx = opens_ff.index[opens_ff.index.get_indexer([t], method="nearest")]
        ref = float(opens_ff.loc[idx[0], s]) if len(idx) else np.nan
        if not np.isfinite(ref) or ref <= 0:
            continue
        buy = f["qty"] > 0
        eps = (f["px"] / ref - 1.0) * (1.0 if buy else -1.0)      # +ve = adverse on either side
        rows.append(dict(symbol=s, date=t, side="BUY" if buy else "SELL", ref=ref, fill=f["px"], eps=eps))
    if not rows:
        print("  (no fills to analyse)", flush=True)
        return
    df = pd.DataFrame(rows)
    df.to_csv(OUT_DIR / "slippage_fills.csv", index=False)
    adv = float((df["eps"] > 0).mean())
    print(f"\n=== REALISED SLIPPAGE (vs the actual open) over {len(df):,} fills ===")
    print(f"  adverse fills      : {adv*100:.1f}%   favorable: {(1-adv)*100:.1f}%   <- emergent p")
    print(f"  mean eps (the drag): {df['eps'].mean()*100:+.4f}%   median {df['eps'].median()*100:+.4f}%")
    print(f"  eps p5 / p95       : {df['eps'].quantile(.05)*100:+.4f}% / {df['eps'].quantile(.95)*100:+.4f}%")
    print(f"  worst adverse      : {df['eps'].max()*100:+.4f}%   best favorable: {df['eps'].min()*100:+.4f}%")
    for side in ("BUY", "SELL"):
        d2 = df[df["side"] == side]
        if len(d2):
            print(f"    {side:<4}: n={len(d2):>6,}  adverse {float((d2['eps']>0).mean())*100:5.1f}%  "
                  f"mean {d2['eps'].mean()*100:+.4f}%")
    if calib.infeasible:
        inf = pd.DataFrame(calib.infeasible)
        inf.to_csv(OUT_DIR / "slippage_infeasible.csv", index=False)
        print(f"  PARTICIPATION GUARD: {len(inf):,} fills exceeded {calib.max_part*100:.0f}% of ADV "
              f"(median {inf['participation'].median()*100:,.1f}%) -> reports/slippage_infeasible.csv")
    print(f"  wrote reports/slippage_fills.csv", flush=True)


def run_backtest(*_a, **_k):
    """REMOVED WITH THE STRATEGY - it called precompute(). Use the pipeline."""
    raise NotImplementedError(
        "run_backtest() was the standalone Nautilus entry point and it recomputed the removed "
        "reference strategy. Run `qbeast-backtest --engine nautilus` instead.")

def main():
    global _OVERLAY_SIZING
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    dyn = any(a.startswith("--slip-dyn") for a in flags)          # --slip-dyn[=SEED]: price-based slippage
    if "--pretax-sizing" in flags:
        # The LEGACY behaviour: size off Nautilus's own pre-tax account equity. Kept only so the old
        # numbers can be reproduced for comparison -- it deploys past CASH_BUFFER on money the reported
        # NAV has already paid in tax, and ran negative cash on 19% of days on smallcap250.
        _OVERLAY_SIZING = False
        print("  [sizing] LEGACY pre-tax sizing ON -- this overstates returns; comparison only.",
              flush=True)
    seed = 7
    for a in flags:
        if a.startswith("--slip-dyn=") and a.split("=", 1)[1].isdigit():
            seed = int(a.split("=", 1)[1])
    if args and args[0] == "reconcile":
        from qbeast_backtest.config import BACKTEST_END, BACKTEST_START
        run_backtest(str(BACKTEST_START.date()), str(BACKTEST_END.date()), reconcile=True,
                     dynamic_slippage=dyn, seed=seed)
    else:
        start = args[0] if args else "2023-01-01"
        end = args[1] if len(args) > 1 else "2025-12-31"
        run_backtest(start, end, reconcile=False, dynamic_slippage=dyn, seed=seed)


if __name__ == "__main__":
    main()
