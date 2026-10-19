r"""
stage3_execution - COSTS, TAX AND THE EXCHANGE
==============================================
The third module. It takes a `StrategyOutput` and turns intentions into an executed book: orders at
T's close, fills at T+1's open, charges, slippage, circuit and staircase backstops, Indian FIFO
capital-gains tax, cash yield on idle balances.

Almost none of the code is here. The arithmetic lives in **qbeast_exec**, the shared team package:

    qbeast_exec.ChargesCalculator    itemised NSE delivery charges (STT, exchange, SEBI, stamp, GST, DP)
    qbeast_exec.SlippageModel        Corwin-Schultz spread + sqrt size impact
    qbeast_exec.TaxEngine            FIFO lots, the 23-Jul-2024 rate change, s.74 carry-forward
    qbeast_exec.PriceBandModel       Tier-1 circuit locks
    qbeast_exec.StaircaseFlag        the operator-ladder gate (a BACKSTOP here - stage 1 is the gate)
    qbeast_exec.Ledger / nautilus    the two execution paths and the T->T+1 contract
    qbeast_exec.survivorship         point-in-time membership (APPLIED in stage 1, code lives there)
    qbeast_exec.report / universe    performance statistics and the per-universe roll-up

That is deliberate and is the answer to "everyone on the same page": there is exactly one
implementation of what a trade costs and what tax it owes, it is versioned, it has its own 211-test
suite, and every strategy and both engines read it. This module is the WIRING, not the logic.

WHY THE STAIRCASE IS STILL CHECKED HERE
---------------------------------------
Stage 1 removes flagged names from the candidate pool, which is the fix. The ledger check stays as a
backstop: it costs nothing, and it catches any future path that reaches execution without going
through stage 1. Both counts are reported, and in a correct run the backstop should refuse ZERO buys -
if it refuses any, something bypassed the gate and that is worth knowing.
"""
from .runner import execute

__all__ = ["execute"]
