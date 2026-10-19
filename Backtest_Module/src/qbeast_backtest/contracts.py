r"""
Backtest_Modular/contracts.py
=============================
THE WIRING. Three frozen dataclasses, one per stage boundary. Stage 1 produces a `MarketData`,
stage 2 consumes it and produces a `StrategyOutput`, stage 3 consumes that and produces an
`ExecutionResult`. Nothing crosses a boundary except through these objects.

WHY TYPED OBJECTS AND NOT JUST FUNCTION ARGUMENTS
-------------------------------------------------
The whole point of the split is that a new strategy changes stage 2 ONLY. That is a promise about an
interface, and a promise nobody can check is not a promise. With a loose signature a teammate's
strategy can quietly:

  * reach past stage 1 and load its own prices        -> a different universe from everyone else's
  * recompute its own momentum off unadjusted closes  -> a different data convention
  * skip the eligibility set                          -> buying names the staircase just flagged
  * hand stage 3 weights that do not sum to anything  -> leverage nobody authorised

Every one of those has happened in this project in some form. A frozen dataclass with a `validate()`
makes each of them a loud failure at the boundary instead of a plausible-looking number three weeks
later.

THE ONE RULE FOR A NEW STRATEGY
-------------------------------
    Read ONLY what `MarketData` gives you. Return a `StrategyOutput`. Touch nothing else.

If a strategy needs an input stage 1 does not provide, the right move is to ADD IT TO STAGE 1 - where
every strategy gets it, on one convention - not to fetch it privately in stage 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

import pandas as pd


# ===================================================================================================
# stage 1 -> stage 2
# ===================================================================================================
@dataclass(frozen=True)
class MarketData:
    """Everything a strategy is allowed to see. Produced by stage1_data, consumed by stage2_strategy.

    All frames are indexed by the TRADING CALENDAR (the benchmark's sessions inside the window) and
    columned by symbol, already forward-filled where that is correct. A strategy never reindexes or
    re-reads from disk: if the frame is wrong, stage 1 is wrong, and fixing it there fixes it for
    everyone.
    """
    calendar: pd.DatetimeIndex          # the sessions this run trades on (from the BENCHMARK)
    opens: pd.DataFrame                 # fill prices: what a market-on-open order gets at T+1
    closes: pd.DataFrame                # decision prices ON THE CALENDAR, forward-filled
    volumes: pd.DataFrame               # for liquidity / slippage / the staircase
    benchmark: pd.Series                # the measurement series AND the calendar's source
    signal_index: pd.Series             # what the trend gate and vol regime read (may differ)

    # --- TRADABILITY, resolved per session by stage 1 --------------------------------------------
    # `eligible[t]` is the set of symbols a strategy MAY hold on session t. It is the intersection of:
    #   * point-in-time index membership (survivorship)
    #   * minus symbols flagged by the staircase gate      <- the thing that was coded but unused
    #   * minus anything stage 1 judged untradeable
    # A strategy ranks WITHIN this set. It does not get to widen it.
    eligible: Mapping[pd.Timestamp, frozenset]
    rebalance_dates: Sequence[pd.Timestamp]

    # --- CAN THE LAST SESSION'S DECISION BE FILLED? -----------------------------------------------
    # A decision is made on session T's close and filled at T+1's open. In a BACKTEST, a decision on
    # `calendar[-1]` can never be filled: there is no T+1 in the data, so acting on it would be a
    # trade at a price nobody has. Strategies must skip it.
    #
    # A LIVE book is the exact opposite case. Its newest session IS `calendar[-1]`, and the decision
    # taken there is the only one that matters - tomorrow's open has not happened yet, which is why
    # there are orders to write at all. Skipping it would make a paper book that never trades.
    #
    # The difference is a property of the DATA, not of the strategy, so it is published here rather
    # than guessed at by each strategy. Use `StrategyBase.decision_sessions(md)` and it is handled.
    last_session_fillable: bool = False

    # --- PRE-WINDOW HISTORY, and why it is a separate field ---------------------------------------
    # `closes` above is the TRADING WINDOW. Any trailing signal must be computed on `history_closes`,
    # which runs from the start of the data, and only THEN read on the calendar.
    #
    # Getting this wrong is silent and expensive, and the first version of this contract got it wrong:
    # it shipped only the window, so 12-1 momentum had nothing to warm up on. MEASURED on midcap150
    # from 2016-09-30: computing momentum on the sliced window pushed the first session with a full
    # 20-name ranking out to 2017-10-06 - 371 days - so the book ran short or empty for its first
    # year and finished at Rs 1.04M against the monolith's Rs 3.12M. Nothing errored; the strategy
    # simply had no signal and dutifully held almost nothing.
    #
    # Hence: the window for decisions, the history for signals, and a validate() that refuses a run
    # whose history does not reach far enough back.
    history_closes: pd.DataFrame = field(default_factory=pd.DataFrame)
    history_volumes: pd.DataFrame = field(default_factory=pd.DataFrame)
    warmup_bars: int = 400              # what the longest trailing signal needs; see signals.py

    # --- provenance, so a number can always be traced back ---------------------------------------
    universe: str = ""
    benchmark_name: str = ""
    signal_index_name: str = ""
    survivorship: bool = False
    staircase_flagged: Mapping[pd.Timestamp, frozenset] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def validate(self) -> list[str]:
        """Problems that would make a run WRONG rather than merely fail. [] = healthy."""
        p: list[str] = []
        if not len(self.calendar):
            p.append("calendar is empty - no sessions in the configured window")
            return p
        for name in ("opens", "closes", "volumes"):
            f = getattr(self, name)
            if f is None or f.empty:
                p.append(f"{name} frame is empty")
                continue
            if not self.calendar.isin(f.index).all():
                missing = int((~self.calendar.isin(f.index)).sum())
                p.append(f"{name} is missing {missing} of {len(self.calendar)} calendar sessions")
        # WARM-UP. A trailing signal computed without pre-window history is not a weak signal, it is
        # an absent one - and it fails quietly, as a book that holds too few names. See the field note.
        if self.history_closes is None or self.history_closes.empty:
            p.append("history_closes is empty - trailing signals (momentum, moving averages, EWMA "
                     "vol) would have no warm-up and the book would run short at the start")
        else:
            before = int((self.history_closes.index < self.calendar[0]).sum())
            if before < self.warmup_bars:
                p.append(f"only {before} session(s) of history before the window opens "
                         f"({self.calendar[0].date()}), but the longest trailing signal needs "
                         f"{self.warmup_bars}. The first ~{max(0, self.warmup_bars - before)} "
                         f"sessions would rank too few names. Move BACKTEST_START later, or source "
                         f"more history.")
        if self.benchmark is None or self.benchmark.dropna().empty:
            p.append("benchmark series is empty - it is the calendar and the measurement series")
        if self.signal_index is None or self.signal_index.dropna().empty:
            p.append("signal_index is empty - the trend gate would have nothing to read")
        if not self.rebalance_dates:
            p.append("no rebalance dates in the window - the book would never trade")
        # Every rebalance session must have an eligibility set. A MISSING key is the dangerous case:
        # `eligible.get(t, everything)` would silently restore the unfiltered universe and undo the
        # staircase gate on exactly the sessions that matter.
        miss = [t for t in self.rebalance_dates if t not in self.eligible]
        if miss:
            p.append(f"{len(miss)} rebalance session(s) have no eligibility set "
                     f"(first {miss[0].date()}): a strategy would have to guess the universe")
        return p


# ===================================================================================================
# stage 2 -> stage 3
# ===================================================================================================
@dataclass(frozen=True)
class StrategyOutput:
    """What a strategy decided. Produced by stage2_strategy, consumed by stage3_execution.

    `targets[t]` is {symbol: weight of NAV} decided on session t's close, to be filled at the next
    session's open. An EMPTY dict is meaningful and is not the same as a missing key:
        {}        -> go to cash; sell everything
        no key    -> not a decision session; hold
    Conflating those is how a flatten instruction gets read as "hold" - this project has fixed that
    exact bug once already, in the Nautilus adapter.
    """
    targets: Mapping[pd.Timestamp, Mapping[str, float]]
    # Per-session diagnostics the reports read. Optional, but a strategy that reports nothing cannot be
    # debugged by anyone but its author.
    exposure: pd.Series | None = None       # intended deployment, 0..1 of NAV
    posture: pd.Series | None = None        # a label: "cash" / "sized" / "full"
    diagnostics: pd.DataFrame | None = None  # anything else worth plotting, REINDEXED to the calendar

    # SERIES THE EXECUTION LAYER NEEDS AT FULL RESOLUTION, un-reindexed.
    # `diagnostics` above is a reporting frame: reindexed to the trading calendar and forward-filled,
    # which is right for plotting and wrong for anything evaluated with `.asof()`. The drawdown
    # breaker's re-entry rule reads `trend_up.asof(d)` DAILY, including on dates the signal index has
    # but the trading calendar does not - so handing it the reindexed frame changes its decisions.
    # Measured: it moved the Nautilus arm 2.44% away from the monolith at the end (worst sessions
    # clustered in Jan-2022, a drawdown) while the internal arm stayed exact to the rupee.
    # Keys the execution layer looks for: "trend_up".
    execution_inputs: Mapping[str, object] = field(default_factory=dict)
    name: str = ""
    params: Mapping[str, object] = field(default_factory=dict)

    def validate(self, md: MarketData, *, cash_buffer: float = 1.0,
                 tol: float = 1e-6) -> list[str]:
        """Check the decisions against the data they were made from."""
        p: list[str] = []
        if not self.targets:
            p.append("no targets at all - the strategy never decided anything")
        for t, w in self.targets.items():
            if t not in md.calendar:
                p.append(f"target dated {t} is not a session on the trading calendar")
                continue
            if not w:
                continue                                  # {} = flatten, legitimate
            tot = float(sum(w.values()))
            if tot > cash_buffer + 1e-6:
                p.append(f"{t.date()}: weights sum to {tot:.4f}, above the {cash_buffer:.4f} cash "
                         f"buffer - that is leverage nobody authorised")
            if any(v < -tol for v in w.values()):
                p.append(f"{t.date()}: negative weight - this framework is long-only")
            # THE GATE THAT WAS BEING IGNORED. A strategy may only hold what stage 1 declared eligible.
            allowed = md.eligible.get(t)
            if allowed is not None:
                stray = sorted(set(w) - set(allowed))
                if stray:
                    p.append(f"{t.date()}: {len(stray)} name(s) outside the eligible set "
                             f"({', '.join(stray[:4])}) - survivorship or the staircase gate was "
                             f"bypassed")
        return p


# ===================================================================================================
# stage 3 output
# ===================================================================================================
@dataclass(frozen=True)
class ExecutionResult:
    """What actually happened once costs, taxes and an exchange got involved."""
    nav: pd.Series                      # after tax - the headline series
    nav_pretax: pd.Series               # same fills, gross
    invested: pd.Series                 # deployed fraction, as EXECUTED (not as intended)
    benchmark: pd.Series                # rebased to the opening capital
    ledger: object                      # the Ledger / replay object, for the friction breakdown
    engine: str = ""                    # "nautilus" | "internal"
    fills: pd.DataFrame | None = None
    refused: pd.DataFrame | None = None  # orders the tradability gates refused, with the reason
    notes: tuple[str, ...] = ()

    def validate(self) -> list[str]:
        p: list[str] = []
        if self.nav is None or self.nav.dropna().empty:
            p.append("NAV is empty - nothing was executed")
            return p
        if (self.nav <= 0).any():
            p.append(f"NAV goes non-positive ({int((self.nav <= 0).sum())} session(s)) - a book cannot "
                     f"be worth nothing and keep trading; check sizing and cash")
        if not self.nav.index.is_monotonic_increasing:
            p.append("NAV index is not sorted")
        return p


# ===================================================================================================
# the strategy interface
# ===================================================================================================
class Strategy(Protocol):
    """What stage 2 must implement. A new strategy is a class satisfying this - nothing else changes.

    Deliberately narrow: `decide` gets a MarketData and returns a StrategyOutput. There is no hook for
    "and also load this file" or "and also place this order", because those are the two things that
    must stay identical across strategies for any comparison between them to mean anything.
    """

    name: str

    def decide(self, md: MarketData) -> StrategyOutput:
        ...
