r"""
stage2_strategy/base.py
=======================
The base class a strategy may inherit. Optional - satisfying `contracts.Strategy` is enough - but it
carries the two things every strategy needs and nobody should re-derive:

  * `eligible_on(md, t)`, which reads the published set rather than guessing at the universe
  * `self_check(md, out)`, which runs the contract's validation and raises instead of returning a
    quietly-wrong book

WHY `eligible_on` IS A METHOD AND NOT A CONVENIENCE
---------------------------------------------------
Because the obvious alternative is wrong in a way that looks right:

    elig = md.eligible.get(t, set(md.closes.columns))     # <- NO

A missing key means stage 1 did not publish a set for that session, and defaulting to "the whole
universe" silently restores every name the staircase and the membership filter just removed. The
default must be EMPTY and loud, not permissive and quiet. `MarketData.validate()` already refuses to
start a run whose rebalance sessions lack a set, and this is the second line of the same defence.
"""
from __future__ import annotations

import pandas as pd

from qbeast_backtest.contracts import MarketData, StrategyOutput


class StrategyBase:
    name: str = "unnamed"

    # ---------------------------------------------------------------------------------------------
    @staticmethod
    def eligible_on(md: MarketData, t: pd.Timestamp) -> frozenset:
        """The names this strategy may hold on session t. Empty, loudly, if stage 1 published none."""
        s = md.eligible.get(t)
        if s is None:
            raise KeyError(
                f"no eligible set for {pd.Timestamp(t).date()}. Stage 1 publishes one per rebalance "
                f"session; a strategy must not substitute the full universe, because that would undo "
                f"the staircase gate and point-in-time membership for exactly that session.")
        return frozenset(s)

    # ---------------------------------------------------------------------------------------------
    @staticmethod
    def decision_sessions(md: MarketData) -> list:
        """The rebalance sessions this strategy should actually decide on.

        Every rebalance date except, in a backtest, one landing on the final session: a decision there
        fills at a next open the data does not contain. `md.last_session_fillable` says when that does
        not apply - a LIVE book's newest session is exactly the one it must decide on, because the open
        it fills at is tomorrow's.

        Loop over this rather than `md.rebalance_dates` and both cases are right. Hand-rolling
        `if t == md.calendar[-1]: continue` is right for a backtest and silently makes a paper book
        that never places an order."""
        last = md.calendar[-1]
        return [t for t in md.rebalance_dates
                if t != last or bool(md.last_session_fillable)]

    # ---------------------------------------------------------------------------------------------
    def self_check(self, md: MarketData, out: StrategyOutput, *, cash_buffer: float = 1.0,
                   strict: bool = True) -> list[str]:
        """Validate this strategy's own output against the data it saw. Raises when `strict`.

        Called by the pipeline, but a strategy can call it in development too. Failing here is cheap;
        failing at the contract boundary after a 90-second Nautilus run is not."""
        probs = out.validate(md, cash_buffer=cash_buffer)
        if probs and strict:
            raise ValueError(f"strategy {self.name!r} violated the stage-2 contract:\n  - "
                             + "\n  - ".join(probs))
        return probs

    # ---------------------------------------------------------------------------------------------
    def decide(self, md: MarketData) -> StrategyOutput:   # pragma: no cover - interface
        raise NotImplementedError("a strategy must implement decide(MarketData) -> StrategyOutput")
