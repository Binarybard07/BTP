r"""
stage1_data - DATA AND TRADABILITY
==================================
The first module. It answers two questions and nothing else:

    1. WHAT ARE THE PRICES?     one convention, one calendar, one place to fix a data fault
    2. WHAT MAY BE TRADED?      per session: point-in-time membership, minus the staircase, minus
                                anything else that is not honestly tradeable

Its whole output is a `contracts.MarketData`. A strategy sees that and no more.

WHY TRADABILITY LIVES HERE AND NOT IN THE STRATEGY
--------------------------------------------------
Because it is not a view. Whether a stock was in the index on a date, and whether its price is an
operator ladder rather than a market, are facts about the DATA - and they must be the same facts for
every strategy, or no comparison between two strategies means anything.

It is also the only placement that WORKS. The staircase gate existed before this module and was wired
into the execution layer, where it refused the fill AFTER the name had already won one of the 20
slots: the slot was wasted, the cash sat idle, the book ran 19 names, and the 21st-ranked perfectly
tradeable name never got in. Refusing a fill protects you from owning the thing; removing it from the
candidate pool also lets you own something else instead.

    old:  rank all -> top 20 -> order -> ledger refuses the buy  -> 19 names, idle cash
    new:  flag -> remove from pool -> rank -> top 20 -> order     -> 20 names, fully deployed

The execution-layer check is KEPT as a backstop. It costs nothing, and it catches any future path that
reaches the ledger without coming through here.
"""
from . import staircase_log
from .loader import load_market_data
from .tradability import StaircaseReport, TradabilityGate

__all__ = ["load_market_data", "TradabilityGate", "StaircaseReport", "staircase_log"]
