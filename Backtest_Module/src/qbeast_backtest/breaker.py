r"""
qbeast_backtest/breaker.py
-----------------------
The PORTFOLIO drawdown breaker: one state machine, used everywhere.

WHAT IT IS
    A full exit to cash when the BOOK falls `DD_EXIT` from its own peak NAV, and a rule for coming back.
    The trigger is measured on the portfolio, not the index -- a book that de-risked into a crash may
    never reach the trigger the index does, and that is the point of measuring it here.

WHY IT IS A MODULE
    The same rule has to run in three places: the research backtest (engine.Ledger), the Nautilus
    execution path (RegimeExec), and the dd_breaker.py sweep. Three copies of a stateful rule is three
    chances for the sweep to report a breaker the strategy does not actually trade. So it lives once.

WHAT THE EVIDENCE SAYS -- READ BEFORE ENABLING
    Measured on this book, a breaker has never paid for itself. On smallcap250/nifty500, 0 of 9 tested
    configurations improved BOTH drawdown and CAGR; a -15% exit cost ~15pp of CAGR to save ~4pp of
    drawdown. A breaker sells AFTER the loss, usually near the low, and then has to decide when to
    return. `dd_frontier.py` (a standing deployment cap) buys drawdown reduction without that timing
    problem and dominated the breaker in every test.

    So DD_EXIT is OFF by default, and turning it on should be a decision you can defend with a sweep on
    YOUR universe -- `python dd_sweep.py --triggers ...` -- not a hunch.

RE-ENTRY RULES
    "gate"     the configured trend gate says uptrend again. Most conservative; can sit out a V-shaped
               recovery for months.
    "recover"  the signal index has risen DD_RECOVER off its lowest close since the halt. Faster, and
               will re-enter into a bear rally.
    "both"     gate up AND index recovered.
"""
from __future__ import annotations

import numpy as np


class DrawdownBreaker:
    """Daily state machine. Feed it NAV and it tells you when to flatten and when to come back.

    Stateful and PATH-DEPENDENT by nature: the peak it measures against is the peak this book actually
    made, so it cannot be precomputed from signals. One instance per simulated book -- never share one
    across two runs, or the second inherits the first's peak.
    """

    __slots__ = ("trigger", "reentry", "recover_pct", "halted", "peak", "trough", "events",
                 "halted_days")

    def __init__(self, trigger: float | None, reentry: str = "gate", recover_pct: float = 0.05) -> None:
        self.trigger = None if trigger is None else abs(float(trigger))
        self.reentry = str(reentry).lower()
        if self.reentry not in ("gate", "recover", "both"):
            raise ValueError(f"reentry must be 'gate' / 'recover' / 'both', got {reentry!r}")
        self.recover_pct = float(recover_pct)
        self.halted = False
        self.peak = -np.inf
        self.trough = np.inf              # lowest signal-index close since the halt
        self.events: list[dict] = []
        self.halted_days = 0

    @property
    def enabled(self) -> bool:
        return self.trigger is not None

    def step(self, date, nav: float, index_px: float, gate_up: bool) -> str | None:
        """Advance one session. Returns "HALT", "RESUME" or None.

        HALT   -> flatten to cash at the next open, and do NOT rebalance this session.
        RESUME -> trading may resume; the next rebalance date rebuilds the book normally.

        Call this once per session, AFTER marking NAV for the day and BEFORE deciding a rebalance, so
        the drawdown is measured on the same NAV the reports show."""
        nav = float(nav)
        if np.isfinite(nav):
            self.peak = max(self.peak, nav)
        dd = (nav / self.peak - 1.0) if (np.isfinite(nav) and self.peak > 0) else 0.0
        px = float(index_px) if index_px is not None else np.nan

        if self.halted:
            self.halted_days += 1
            if np.isfinite(px):
                self.trough = min(self.trough, px)
            off_low = (px / self.trough - 1.0) if (np.isfinite(px) and np.isfinite(self.trough)
                                                   and self.trough > 0) else np.nan
            recovered = bool(np.isfinite(off_low) and off_low >= self.recover_pct)
            fire = {"gate": bool(gate_up), "recover": recovered,
                    "both": bool(gate_up) and recovered}[self.reentry]
            if fire:
                self.halted = False
                self.trough = np.inf
                self.events.append(dict(date=date, event="RESUME", rule=self.reentry, dd=dd,
                                        index_off_low=off_low))
                return "RESUME"
            return None

        if self.trigger is not None and dd <= -self.trigger:
            self.halted = True
            self.trough = px if np.isfinite(px) else np.inf
            self.events.append(dict(date=date, event="HALT", rule=self.reentry, dd=dd,
                                    index_off_low=0.0))
            return "HALT"
        return None

    # ---------------------------------------------------------------- reporting
    def summary(self) -> dict:
        return dict(trigger=self.trigger, reentry=self.reentry, recover_pct=self.recover_pct,
                    halts=sum(1 for e in self.events if e["event"] == "HALT"),
                    resumes=sum(1 for e in self.events if e["event"] == "RESUME"),
                    days_halted=self.halted_days)


def from_config() -> DrawdownBreaker:
    """The breaker this project is configured to trade (config.DD_EXIT / DD_REENTRY / DD_RECOVER).

    Always returns an instance; `.enabled` is False when DD_EXIT is None, and a disabled breaker is a
    no-op whose presence costs one comparison a day. That keeps the caller free of `if breaker is not
    None` branches, which is where an "off" feature quietly becomes an "ignored" one."""
    from qbeast_backtest.config import DD_EXIT, DD_REENTRY, DD_RECOVER
    return DrawdownBreaker(DD_EXIT, DD_REENTRY, DD_RECOVER)
