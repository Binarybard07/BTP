r"""
stage1_data/tradability.py
==========================
WHO MAY BE TRADED, per session. This is where the staircase gate finally does the job it was written
for, and where survivorship is applied.

THE ORDER MATTERS
-----------------
    all priced symbols
      -> point-in-time index membership        (survivorship: was it in the index THAT DAY?)
      -> minus staircase-flagged names         (is the price a market, or an operator ladder?)
      -> minus anything with no usable price
      = the ELIGIBLE SET handed to the strategy

Every step narrows. Nothing widens. A strategy ranks inside the result.

CAUSALITY
---------
Everything here reads sessions up to and INCLUDING the decision date t, never past it. The staircase
flag for t is computed from the trailing window ending at t; the order it suppresses would have filled
at t+1's open. There is no path by which tomorrow's bar informs today's exclusion.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class StaircaseReport:
    """What the gate did, per session and in total. Printed by the runner and written to CSV.

    A gate whose effect is invisible is a gate nobody trusts - and this one had been ON for months
    while changing nothing anybody could see, because it only ever refused fills that the reports did
    not break out."""
    flagged: dict = field(default_factory=dict)         # {date: frozenset(symbols flagged that day)}
    excluded_from_pool: dict = field(default_factory=dict)  # {date: frozenset} - flagged AND eligible
    episodes: pd.DataFrame | None = None                # the gate's own scan(), for the audit trail

    @property
    def sessions_with_flags(self) -> int:
        return sum(1 for v in self.flagged.values() if v)

    @property
    def symbols_ever_flagged(self) -> frozenset:
        out: set = set()
        for v in self.flagged.values():
            out |= set(v)
        return frozenset(out)

    @property
    def rebalances_affected(self) -> int:
        return sum(1 for v in self.excluded_from_pool.values() if v)

    def summary(self) -> str:
        n_sym = len(self.symbols_ever_flagged)
        if not n_sym:
            return ("staircase: 0 symbols flagged in this universe over this window - the gate ran and "
                    "found nothing. That is a real result for a large-cap universe, where an operator "
                    "ladder is rare; it is NOT evidence the gate works. Run it on a micro-cap set to "
                    "see it bite.")
        worst = sorted(((len(v), str(k.date())) for k, v in self.excluded_from_pool.items() if v),
                       reverse=True)[:1]
        return (f"staircase: {n_sym} symbol(s) flagged on {self.sessions_with_flags} session(s); "
                f"{self.rebalances_affected} rebalance(s) had a name removed from the candidate pool"
                + (f" (worst {worst[0][0]} on {worst[0][1]})" if worst else "")
                + f". Names: {', '.join(sorted(self.symbols_ever_flagged)[:8])}"
                + (" ..." if n_sym > 8 else ""))


class TradabilityGate:
    """Turns raw frames + config into a per-session eligible set.

    Built once per run. `eligible_on(t)` is cheap after the first call for t.
    """

    def __init__(self, closes: pd.DataFrame, ohlcv: dict, *, membership=None,
                 staircase=None, exclude_flagged: bool = True,
                 min_price: float = 0.0) -> None:
        self.closes = closes
        self.ohlcv = ohlcv
        self.membership = membership            # shared.membership.Membership, or None
        self.staircase = staircase              # qbeast_exec.StaircaseFlag, or None
        self.exclude_flagged = bool(exclude_flagged)
        self.min_price = float(min_price)
        self._all = frozenset(str(c).upper() for c in closes.columns)
        self.report = StaircaseReport()
        if staircase is not None:
            try:
                self.report.episodes = staircase.scan()
            except Exception:                   # noqa: BLE001 - an audit table must never fail a run
                self.report.episodes = None

    # -------------------------------------------------------------------------------------------
    def flagged_on(self, t: pd.Timestamp) -> frozenset:
        """Symbols the staircase gate says are climbing their upper circuit as of session t.

        Uses `StaircaseFlag.flagged_symbols(t)`, which is the gate's OWN predicate - verified
        identical to the one behind its trading decision:

            flagged(sym, date)   -> hits >= min_hits or run >= min_run      # what can_trade() asks
            flagged_symbols(date)-> hits >= min_hits or run >= min_run      # what this asks

        Two reasons not to call `check()` in a loop instead: `check()` APPENDS to the gate's `blocked`
        audit list, so a candidacy test would forge thousands of fake "refused order" rows; and it is
        one call per symbol per day against one call per day."""
        if self.staircase is None:
            return frozenset()
        if t in self.report.flagged:
            return self.report.flagged[t]
        try:
            out = frozenset(str(s).upper() for s in self.staircase.flagged_symbols(t))
        except Exception as e:                  # noqa: BLE001
            # A gate that cannot answer must not silently pass everything - that is the failure mode
            # this module exists to end. Say so loudly and treat the day as unfiltered, once.
            print(f"  ** staircase gate failed on {pd.Timestamp(t).date()} "
                  f"({type(e).__name__}: {e}); NO exclusion applied for that session **", flush=True)
            out = frozenset()
        self.report.flagged[t] = out
        return out

    # -------------------------------------------------------------------------------------------
    def eligible_on(self, t: pd.Timestamp, row: pd.Series | None = None) -> frozenset:
        """The set a strategy may hold on session t."""
        # 1. survivorship: what was in the index on this date
        if self.membership is not None:
            elig = {str(s).upper() for s in self.membership.eligible(t)}
        else:
            elig = set(self._all)

        # 2. a usable price. A name with no close cannot be ranked or sized; leaving it in the pool
        #    makes the top-N quietly shorter than N.
        if row is not None:
            priced = {str(s).upper() for s, v in row.items()
                      if pd.notna(v) and float(v) > self.min_price}
            elig &= priced

        # 3. THE STAIRCASE. Removed from the POOL, not refused at the fill - so the next-ranked
        #    tradeable name takes the slot and the book stays deployed.
        if self.exclude_flagged:
            flagged = self.flagged_on(t)
            hit = frozenset(elig & set(flagged))
            if hit:
                self.report.excluded_from_pool[t] = hit
                elig -= set(flagged)
        return frozenset(elig)

    # -------------------------------------------------------------------------------------------
    def build(self, calendar: pd.DatetimeIndex, closes_ff: pd.DataFrame,
              only: list | None = None) -> dict:
        """{session: eligible set}. `only` restricts the work to the rebalance sessions, which is all
        a strategy actually asks about - the staircase test is per symbol per day and is the one
        genuinely expensive thing in stage 1."""
        days = list(only) if only is not None else list(calendar)
        out = {}
        for t in days:
            row = closes_ff.loc[t] if t in closes_ff.index else None
            out[t] = self.eligible_on(t, row)
        return out
