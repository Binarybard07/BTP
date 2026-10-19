"""
qbeast_backtest - a three-module backtest framework where only the strategy changes.

    stage1_data        prices, calendar, survivorship, the staircase gate -> the ELIGIBLE SET
    stage2_strategy    THE ONLY PART A NEW STRATEGY CHANGES
    stage3_execution   the T->T+1 loop, costs, tax, Nautilus or the internal ledger

The boundaries are typed and validated (see `contracts`), so a strategy that bypasses the eligible
set, returns leverage, or reads data it was not given fails AT the boundary rather than three stages
later as an implausible number.

    from qbeast_backtest import pipeline
    r = pipeline.run(strategy="my_strategy", engine="internal")

Costs, charges, slippage, tax and the Nautilus harness live in the separate top-level `qbeast_exec`
package - the shared team library, one implementation, read by every strategy.
"""
__version__ = "1.0.0"

__all__ = ["contracts", "pipeline", "stage1_data", "stage2_strategy", "stage3_execution"]
