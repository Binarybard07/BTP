"""
qbeast_exec.ledger — the cash/position/tax ledger, and the Nautilus fill replay.

WHY THIS IS IN THE SHARED LIBRARY
    `export_books` could already turn a finished Nautilus engine into readable books, and
    `NautilusFeeModel` / `NautilusFillModel` could already charge it correctly. But neither answers the
    question a strategy actually needs answered: **what is my NAV?** Nautilus models neither
    capital-gains tax nor a yield on idle cash, so its own account equity is a PRE-TAX, interest-free
    number. Reading it and calling it a result overstates return and -- because position sizing reads
    equity -- silently levers the book.

    So the overlay has to live somewhere. It used to live in the project, which meant the shared library
    was not enough to run a Nautilus strategy end to end. It is here now.

WHAT IT IS
    A transparent cash-account simulation: integer share positions, no shorting, no leverage. Every
    rupee of commission, slippage and tax hits the cash balance, so the equity curve is net of
    everything. Capital gains go through `TaxEngine` -- the same FIFO lot book and the same statutory
    rules as everywhere else, not a second implementation.

CONFIG-FREE BY CONSTRUCTION
    Every rule arrives as an explicit config object. There is no module-level import of any project
    settings, because a library that reads someone's `config.py` is not a library. That also makes a
    sweep safe: rewriting a config file between runs cannot leave this holding a stale value.

        from qbeast_exec import Ledger, TaxConfig, ChargesConfig
        led = Ledger(1_000_000, tax_config=TaxConfig(),
                     charges_config=ChargesConfig.equity_delivery(), cash_yield=0.04)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .charges import ChargesCalculator, ChargesConfig
from .tax import TaxConfig, TaxEngine

__all__ = ["Ledger", "apply_fill", "split_commission", "replay_fills", "aftertax_nav"]

TRADING_DAYS = 252.0


class Ledger:
    """Cash, integer positions, FIFO capital-gains tax, a daily yield on idle cash.

    `tax=False` runs the same fills with the capital-gains deduction switched OFF -- the GROSS curve.
    Identical trades, so the difference between the two is exactly the tax drag and nothing else.
    """

    def __init__(self, capital: float, *, tax_config: TaxConfig | None = None,
                 charges_config: ChargesConfig | None = None, cash_yield: float = 0.0,
                 tax: bool = True, bands=None, staircase=None) -> None:
        self.tax_on = bool(tax)
        self.cash_yield = float(cash_yield)
        # TIER-1 tradability gates, both optional. A stock LOCKED at its circuit can only be traded on
        # one side; a name climbing its upper circuit in steps should not be bought into at all. Momentum
        # ranks exactly those names top, which is why the gates exist rather than being assumed away.
        self.bands = bands
        self.staircase = staircase
        self.blocked_orders: list[dict] = []

        self.cash = float(capital)
        self.pos: dict[str, int] = {}
        self.tax = TaxEngine(tax_config or TaxConfig())
        self.charges = ChargesCalculator(charges_config or ChargesConfig.equity_delivery())
        self.tax_paid = self.cost_paid = self.interest = 0.0
        self.nav_hist: dict[pd.Timestamp, float] = {}
        self.invested_hist: dict[pd.Timestamp, float] = {}
        self.trades: list[dict] = []
        self.tax_events: list[dict] = []

    # ---- views onto the shared tax ledger -------------------------------------------------------
    @property
    def lots(self) -> dict:
        """sym -> [Lot(date, qty, price), ...], FIFO."""
        return self.tax.lots

    @property
    def realized(self) -> dict:
        """fy -> {short_old, short_new, long_old, long_new} -- gains split by SALE-DATE tax regime."""
        return self.tax.realized

    @property
    def assessed(self) -> set:
        return set(self.tax.assessed)

    @property
    def lot_closes(self) -> list[dict]:
        return [dict(symbol=c.symbol, buy_date=c.buy_date, sell_date=c.sell_date,
                     days_held=c.days_held, qty=c.qty, buy_px=c.buy_price, sell_px=c.sell_price,
                     gain=c.gain, term=c.term) for c in self.tax.closes]

    # ---- daily accrual -------------------------------------------------------------------------
    @staticmethod
    def _fy(date: pd.Timestamp) -> int:
        return date.year if date.month >= 4 else date.year - 1

    def _accrue_and_assess(self, date: pd.Timestamp) -> None:
        d_int = self.cash * (self.cash_yield / TRADING_DAYS)
        self.cash += d_int
        self.interest += d_int
        if not self.tax_on:
            return
        # TaxEngine decides WHAT is due and WHEN (once per FY, on/after 31 Mar). This class only moves
        # the cash and records the event, so there is one place the law is written down.
        before = set(self.tax.assessed)
        due = self.tax.assess(date)
        if not due and not (set(self.tax.assessed) - before):
            return
        for fy in sorted(set(self.tax.assessed) - before):
            a = self.tax.assessed[fy]
            self.tax_events.append(dict(
                date=date, fy=fy, realised_stcg=a["realised_stcg"], realised_ltcg=a["realised_ltcg"],
                ltcg_exemption=a["ltcg_exemption_used"], stcg_taxable=a["stcg_taxable"],
                ltcg_taxable=a["ltcg_taxable"], stcg_tax=a["stcg_tax"], ltcg_tax=a["ltcg_tax"],
                tax=a["tax"], stcg_gain=a["stcg_taxable"], ltcg_gain=a["ltcg_taxable"],
                realised_stcg_old=a["stcg_old"], realised_stcg_new=a["stcg_new"],
                realised_ltcg_old=a["ltcg_old"], realised_ltcg_new=a["ltcg_new"],
                exemption_limit=a["ltcg_exemption_limit"]))
        self.cash -= due
        self.tax_paid += due

    def mark(self, date: pd.Timestamp, close_row: pd.Series) -> float:
        """Accrue, assess, then mark to market. Call once per trading day, in order."""
        self._accrue_and_assess(date)
        mtm = sum(q * close_row.get(s, np.nan) for s, q in self.pos.items()
                  if np.isfinite(close_row.get(s, np.nan)))
        nav = self.cash + mtm
        self.nav_hist[date] = nav
        self.invested_hist[date] = mtm
        return nav

    def nav_at(self, price_row: pd.Series) -> float:
        mtm = sum(q * price_row.get(s, np.nan) for s, q in self.pos.items()
                  if np.isfinite(price_row.get(s, np.nan)))
        return self.cash + mtm

    def equity_curve(self) -> pd.Series:
        return pd.Series(self.nav_hist).sort_index()


def split_commission(calc: ChargesCalculator, qty: int, px: float, comm: float,
                     _warned: list | None = None) -> tuple[float, float]:
    """Recover (charges, slippage) from the single commission figure Nautilus reports.

    The fee model builds it as `calc.total(side, qty, px)` plus a cost-based slippage add-on, so
    re-running the first term through the SAME calculator recovers the split exactly -- arithmetic, not
    an estimate. Without this every Nautilus report logs slippage as 0.00 and buries it inside
    commission, and a reader cannot tell what execution cost from what the exchange charged.

    A negative remainder means the charge schedule changed between execution and replay. Attributing
    that to slippage would be fiction, so it is charged wholly as charges and reported once.
    """
    charges = float(calc.total("BUY" if qty > 0 else "SELL", abs(int(qty)), float(px)))
    slip = float(comm) - charges
    if slip < -0.01:
        if _warned is not None and not _warned:
            _warned.append(True)
            print(f"  [fees] WARNING: recomputed charges ({charges:,.2f}) exceed the commission "
                  f"reported ({comm:,.2f}). The charge schedule differs between execution and replay, "
                  f"so the charges/slippage SPLIT is unreliable for this run; the TOTAL is unaffected.",
                  flush=True)
        return float(comm), 0.0
    return charges, max(slip, 0.0)


def apply_fill(led: Ledger, date, symbol: str, qty: int, px: float, comm: float,
               _warned: list | None = None) -> None:
    """Apply ONE fill: cash, FIFO tax lots, position, trade record.

    One implementation, so a strategy's in-flight mirror ledger and the post-run replay can never
    drift. That matters because the equity a strategy SIZES from must be the equity the replay REPORTS.
    """
    led.cash -= comm                     # the commission already carries the slippage add-on
    led.cost_paid += comm
    gs = gl = 0.0
    if qty > 0:
        led.cash -= qty * px
        led.tax.buy(symbol, date, qty, px)
        led.pos[symbol] = led.pos.get(symbol, 0) + qty
    else:
        led.cash += (-qty) * px
        g = led.tax.sell(symbol, date, -qty, px)
        gs, gl = g["short"], g["long"]
        led.pos[symbol] = led.pos.get(symbol, 0) + qty
        if led.pos[symbol] == 0:
            led.pos.pop(symbol, None)
    # ATTRIBUTION ONLY: `cost` stays the full commission, so cash, NAV and every P&L number are
    # untouched. This only stops the reports showing slippage as zero when it was really charged.
    _charge, _slip = split_commission(led.charges, qty, px, comm, _warned)
    led.trades.append(dict(date=date, symbol=symbol, qty=qty, px=px, cost=comm, commission=_charge,
                           slippage=_slip, realized_short=gs, realized_long=gl))


def replay_fills(fills, closes: pd.DataFrame, cal, initial: float, *, opens=None, tax: bool = True,
                 tax_config: TaxConfig | None = None, charges_config: ChargesConfig | None = None,
                 cash_yield: float = 0.0, verbose: bool = True) -> Ledger:
    """Replay Nautilus's fills through the tax + cash-yield layer and return the resulting Ledger.

    WHY A LEDGER AND NOT JUST A NAV: this is the execution SOURCE OF RECORD, so reports must be able to
    read positions, lots, realised gains and tax events off it.

    `fills` is a sequence of dicts with keys ts, symbol, qty, px, comm -- exactly what an
    `on_order_filled` handler collects.

    **Pass `opens`.** Under bar execution Nautilus prices a market order against the newest bar it has
    processed, which is the PREVIOUS session's -- the day's own bar arrives after the clock bar the
    order was submitted on. A price from a session that has already closed is not executable, and in a
    momentum book it is systematically favourable, because it buys names that gapped up. With `opens`
    every fill is repriced to the fill-day open. Nautilus still decides WHICH orders fill and WHEN;
    only the price is corrected.
    """
    led = Ledger(float(initial), tax_config=tax_config, charges_config=charges_config,
                 cash_yield=cash_yield, tax=tax)
    warned: list = []
    by_day: dict = {}
    for f in fills:
        by_day.setdefault(pd.Timestamp(f["ts"]).date(), []).append(f)
    closes_ff = closes.reindex(cal).ffill()
    opens_c = opens.reindex(cal) if opens is not None else None
    repriced = moved = 0
    for t in cal:
        # Sells before buys, so sale proceeds fund the purchases and cash never dips negative
        # intraday on a day that is actually self-financing.
        for f in sorted(by_day.get(t.date(), []), key=lambda x: x["qty"]):
            px = float(f["px"])
            if opens_c is not None:
                o = (float(opens_c.loc[t].get(f["symbol"], float("nan")))
                     if t in opens_c.index else float("nan"))
                if np.isfinite(o) and o > 0 and abs(o - px) > 1e-9:
                    moved += abs(o - px) * abs(int(f["qty"]))
                    repriced += 1
                    px = o
            apply_fill(led, t, f["symbol"], int(f["qty"]), px, float(f["comm"]), warned)
        led.mark(t, closes_ff.loc[t])        # applies the cash yield + the FY tax assessment
    if verbose:
        if opens_c is None and len(by_day):
            print("  [fills] WARNING: no opens panel passed to replay_fills, so fills keep the price "
                  "the engine assigned -- the PREVIOUS session's close, not the fill-day open. "
                  "Pass opens= to price them correctly.", flush=True)
        elif repriced:
            print(f"  [fills] repriced {repriced:,} of {sum(len(v) for v in by_day.values()):,} fills "
                  f"to the fill-day open ({moved:,.0f} of notional moved).", flush=True)
    return led


def aftertax_nav(fills, closes, cal, initial, **kw) -> pd.Series:
    """The after-tax NAV series, for when only the curve is wanted."""
    return replay_fills(fills, closes, cal, initial, **kw).equity_curve()
