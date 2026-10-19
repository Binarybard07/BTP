"""
qbeast_exec.nautilus — the Nautilus harness: instruments, bars, and the T -> T+1 execution contract.

THE PROBLEM THIS SOLVES
    A daily strategy decides on day `t`'s close and can only trade at `t+1`'s open. Getting that wrong
    is the most expensive class of backtest error there is, and under bar execution it happens in TWO
    independent ways that look identical in the output:

      1. WRONG DAY   -- an order submitted on `t`'s bar fills on `t`, so the decision reads a price it
                        then trades at. A same-bar look-ahead.
      2. WRONG PRICE -- an order submitted before the session's own bar has been processed is matched
                        against the newest bar the engine HAS, which is the PREVIOUS session's. The day
                        is right and the price is from a session that has already closed.

    Failure 2 is the nastier one: it is invisible in a date audit, it affects ~99.9% of fills, and in a
    momentum book it is systematically FAVOURABLE because the book buys names that gapped up. Measured
    on one real run before it was fixed: a mean +0.25% discount per fill, worth Rs 84,728 of return that
    did not exist.

THE CONTRACT, AND THE THREE PIECES THAT ENFORCE IT
    `make_bars(..., offset_ns=0)` for the CLOCK instrument and `offset_ns=1` for the tradables. Same
        timestamp, one nanosecond apart, so the clock bar is always processed first and an order
        submitted on it meets the day's own tradable bar -- the OPEN.
    `DeferredSubmit` holds the target decided on `t` and releases it on the NEXT clock bar, so the fill
        lands at `t+1`'s open rather than `t`'s.
    `replay_fills(..., opens=)` in `qbeast_exec.ledger` reprices every fill to the fill-day open, which
        is the only part that fixes failure 2.

    All three are needed. Any one alone leaves a hole.

USAGE

    from qbeast_exec import (make_equity, make_bars, DeferredSubmit, Ledger, replay_fills,
                             NautilusFeeModel, NautilusFillModel, export_books)

    clock = make_equity("NIFTY 50")                      # any series that ticks once per session
    engine.add_instrument(clock)
    engine.add_data(make_bars(clock, ic, ic, ic, ic, None, offset_ns=0))   # FIRST
    for s in symbols:
        inst = make_equity(s); engine.add_instrument(inst)
        engine.add_data(make_bars(inst, opens[s], None, None, closes[s], vols[s], offset_ns=1))

    # inside the strategy
    def on_start(self):
        self.defer = DeferredSubmit()
    def on_bar(self, bar):
        if not self.is_clock(bar):
            return
        for day, target in self.defer.release():          # yields at most the PRIOR day's target
            self.submit_target(day, target)
        if self.is_rebalance(day):
            self.defer.queue(day, target_weights)         # fills next bar, i.e. t+1 open
"""
from __future__ import annotations

__all__ = ["DeferredSubmit", "make_equity", "make_bars", "INR_VENUE", "DAY_BAR_SPEC"]

INR_VENUE = "NSE"
DAY_BAR_SPEC = "1-DAY-LAST"


class DeferredSubmit:
    """Hold a target decided on day `t`; release it on the next clock bar so it fills at `t+1`'s open.

    Deliberately a tiny, engine-free state machine: it is the one piece of the T+1 contract that can be
    unit-tested without Nautilus, and it is the piece most likely to be reimplemented wrongly. The
    invariant it exists to guarantee is that **nothing is ever submitted on the bar it was decided on**.

        d = DeferredSubmit()
        d.queue(day_t, target)        # decided on t
        list(d.release())             # [] on t  -- nothing submitted same-bar
        list(d.release())             # [(day_t, target)] on t+1

    An empty dict is a legitimate target meaning FLATTEN TO CASH, so `queue(day, {})` must survive. An
    earlier version dropped falsy targets and the strategy was then never told to de-risk in a
    downtrend: it rode through crashes and the drawdown blew out from -17.5% to -35%.
    """

    __slots__ = ("_pending", "_released")

    def __init__(self) -> None:
        self._pending: tuple | None = None
        self._released: int = 0

    def queue(self, day, target) -> None:
        """Record the decision made on `day`. Replaces any still-unreleased target for the same day."""
        if target is None:
            raise ValueError("DeferredSubmit.queue: target must be a mapping; use {} to flatten to cash")
        self._pending = (day, target)

    def release(self):
        """Yield the pending decision, at most one, then clear it. Yields nothing on the queueing bar."""
        if self._pending is None:
            return
        item, self._pending = self._pending, None
        self._released += 1
        yield item

    @property
    def waiting(self) -> bool:
        return self._pending is not None

    @property
    def released(self) -> int:
        return self._released

    def __repr__(self) -> str:
        day = self._pending[0] if self._pending else None
        return f"DeferredSubmit(waiting={self.waiting}, for={day}, released={self._released})"


def _day_bar_spec():
    """BarSpecification(1, DAY, LAST). Built on demand, not at import, so this module imports without
    nautilus_trader present."""
    from nautilus_trader.model.data import BarSpecification
    from nautilus_trader.model.enums import BarAggregation, PriceType
    return BarSpecification(1, BarAggregation.DAY, PriceType.LAST)


def make_equity(symbol: str, venue: str = INR_VENUE, currency: str = "INR",
                tick: str = "0.05", precision: int = 2):
    """An NSE-style delivery equity: single-share lots, no derivatives multiplier.

    Imported lazily so this module can be imported -- and `DeferredSubmit` unit-tested -- without
    nautilus_trader installed. `currency` is looked up by NAME on the currencies module, because
    Nautilus exposes them as module constants (`INR`, `USD`, ...) rather than a factory.
    """
    from nautilus_trader.model import currencies as _ccys
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import Equity
    from nautilus_trader.model.objects import Price, Quantity

    try:
        ccy = getattr(_ccys, str(currency).upper())
    except AttributeError as e:                                  # a typo here is silent otherwise
        raise ValueError(f"make_equity: unknown currency {currency!r}") from e

    return Equity(
        instrument_id=InstrumentId(Symbol(symbol), Venue(venue)),
        raw_symbol=Symbol(symbol), currency=ccy,
        price_precision=precision, price_increment=Price.from_str(tick),
        lot_size=Quantity.from_int(1), isin=None, ts_event=0, ts_init=0,
    )


def make_bars(instrument, o, h, l, c, v, offset_ns: int = 0, precision: int = 2):  # noqa: E741
    """Daily bars for one instrument, from pandas Series indexed by date.

    `offset_ns` is the whole T+1 mechanism and is not cosmetic. Nautilus processes same-timestamp data
    in the order it is fed, so the CLOCK instrument takes `offset_ns=0` and every tradable takes
    `offset_ns=1`. An order submitted on the clock bar then meets the day's own tradable bar and fills
    at its OPEN. Give them the same offset and the fill price depends on feed order -- which is to say
    it is undefined.

    Missing OHLC fall back to the close, and highs/lows are widened to contain the open and close,
    because Nautilus rejects a bar whose high is below its open.
    """
    import numpy as np
    import pandas as pd
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.enums import AggregationSource
    from nautilus_trader.model.objects import Price, Quantity

    bar_type = BarType(instrument.id, _day_bar_spec(), AggregationSource.EXTERNAL)
    out = []
    for ts in c.index:
        cc = c.loc[ts]
        if not np.isfinite(cc):
            continue
        oo = o.loc[ts] if (o is not None and np.isfinite(o.loc[ts])) else cc
        hi = h.loc[ts] if (h is not None and np.isfinite(h.loc[ts])) else cc
        lo = l.loc[ts] if (l is not None and np.isfinite(l.loc[ts])) else cc
        hi = max(hi, oo, cc)
        lo = min(lo, oo, cc)
        vol = int(v.loc[ts]) if (v is not None and np.isfinite(v.loc[ts]) and v.loc[ts] > 0) else 1
        ns = int(pd.Timestamp(ts, tz="UTC").value) + offset_ns
        out.append(Bar(bar_type,
                       Price(round(oo, precision), precision), Price(round(hi, precision), precision),
                       Price(round(lo, precision), precision), Price(round(cc, precision), precision),
                       Quantity(vol, 0), ns, ns))
    return out
