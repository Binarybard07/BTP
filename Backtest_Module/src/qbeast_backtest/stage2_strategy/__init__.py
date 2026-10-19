r"""
stage2_strategy - THE ONLY PART A NEW STRATEGY CHANGES
======================================================
A strategy is a class with a `name` and a `decide(MarketData) -> StrategyOutput`. That is the whole
interface. It reads what stage 1 gives it and returns what it wants to hold; it does not load data,
does not place orders, does not compute costs, and does not decide what is tradeable.

TO ADD A STRATEGY
-----------------
    1. write a module here with a class satisfying `contracts.Strategy`
    2. register it in `registry.py`
    3. run `python pipeline.py --strategy your_name`

Nothing in stage 1 or stage 3 changes, which is the point: two strategies run on the same prices, the
same eligibility rules, the same staircase gate, the same charges, slippage, tax and execution engine.
Any difference in the result is then the strategy, and not an accident of plumbing.

WHAT THE CONTRACT WILL REFUSE
-----------------------------
`StrategyOutput.validate()` fails a strategy that returns weights summing above the cash buffer
(unauthorised leverage), negative weights (this framework is long-only), targets on a date that is not
a session, or - the one that matters most here - any name OUTSIDE the eligible set stage 1 published.
That last check is what makes the staircase gate and point-in-time membership binding rather than
advisory.
"""
from .base import StrategyBase
from .registry import available, get

__all__ = ["StrategyBase", "available", "get"]
