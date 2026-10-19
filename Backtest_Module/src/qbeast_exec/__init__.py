r"""
qbeast_exec — shared execution-accounting library for NautilusTrader strategies.

Shared modules, so every strategy on the team handles the same problems IDENTICALLY:

    qbeast_exec.tax           Indian capital-gains tax (FIFO lots, date-dependent rates, FY assessment)
    qbeast_exec.charges       Transaction charges (itemised) + slippage (deterministic or two-sided)
    qbeast_exec.bands         Price bands / circuit locks, and market-wide breakers
    qbeast_exec.survivorship  Point-in-time index membership -- the survivorship-bias control
    qbeast_exec.universe      Per-universe output folders + an honest cross-universe roll-up
    qbeast_exec.books         Any Nautilus engine -> order book, trade book, per-stock roll-up
    qbeast_exec.ledger        Cash/position/FIFO-tax ledger + the Nautilus fill replay
    qbeast_exec.nautilus      Instruments, bars, and the T -> T+1 execution contract
    qbeast_exec.report        Shared performance stats + a self-contained HTML dashboard

NOT IN THIS COPY: `qbeast_exec.dashboard` (the tabbed-page renderer and its panels) was removed before
handoff - it carried the trend-gate vocabulary of a strategy this bundle does not ship. `Dashboard`,
`performance_stats`, `make_equity` and `drawdown_table` are in `qbeast_exec.report` and are unaffected.
This copy is therefore a SUBSET of the canonical library at the same version string, so it cannot be
synced from Backtest_Modular with a plain file copy - see fix_to_send_exec.py.

Nothing here imports any strategy's config — every parameter arrives through a dataclass with defaults,
so the package drops into any project unchanged. Nautilus is only needed for the two adapter classes
(`NautilusFeeModel`, `NautilusFillModel`); everything else is plain pandas and works standalone.

Quick start
-----------
    from qbeast_exec import (TaxEngine, TaxConfig, ChargesConfig, ChargesCalculator,
                             SlippageModel, SlippageConfig, NautilusFeeModel, NautilusFillModel,
                             Dashboard, performance_stats)

See README.md for the full API and worked examples.
"""
from .bands import (BandConfig, MarketWideBreaker, MWCBConfig, PriceBandModel,
                    StaircaseConfig, StaircaseFlag,
                    # the pre-2.4.1 rules, for reconciling numbers produced with them - nothing else
                    StaircaseConfigV1, StaircaseFlagV1)
# The v2.4.1 circuit & liquidity flags library, vendored verbatim (its own 32 install checks and 27
# unit tests pass unmodified), plus the adapter that puts it behind StaircaseFlag.
from . import circuit_flags
from .circuit_flags import (ChronicParams, CircuitLiquidityTracker, CircuitParams, StaircaseParams,
                            four_flag_columns)
from .staircase241 import GATE_OK, Staircase241, Staircase241Config
from .books import export_books
from .charges import (ChargesCalculator, ChargesConfig, NautilusFeeModel, NautilusFillModel,
                      SlippageConfig, SlippageModel, atr_pct, corwin_schultz)
from .ledger import Ledger, aftertax_nav, apply_fill, replay_fills, split_commission
from .nautilus import DeferredSubmit, make_bars, make_equity
from .report import Dashboard, drawdown_table, performance_stats
from .survivorship import (DEFAULT_ALIAS, DEFAULT_JUNK, Membership, SurvivorshipConfig,
                           assert_no_look_ahead, find_membership_file, load_membership,
                           normalise)
from .tax import Lot, LotClose, TaxConfig, TaxEngine
from .universe import UniverseReport

__version__ = "1.1.1"

__all__ = [
    # tax
    "TaxEngine", "TaxConfig", "Lot", "LotClose",
    # charges + slippage
    "ChargesConfig", "ChargesCalculator", "NautilusFeeModel",
    "SlippageConfig", "SlippageModel", "NautilusFillModel",
    "corwin_schultz", "atr_pct",
    # price bands / circuit locks
    "BandConfig", "PriceBandModel", "MWCBConfig", "MarketWideBreaker",
    "StaircaseConfig", "StaircaseFlag",
    "StaircaseConfigV1", "StaircaseFlagV1",
    "Staircase241", "Staircase241Config", "GATE_OK", "circuit_flags",
    "CircuitLiquidityTracker", "CircuitParams", "StaircaseParams",
    "ChronicParams", "four_flag_columns",
    # nautilus harness: instruments, bars, the T -> T+1 contract, books
    "make_equity", "make_bars", "DeferredSubmit", "export_books",
    # the cash/position/tax ledger + the fill replay that applies the overlay
    "Ledger", "apply_fill", "replay_fills", "aftertax_nav", "split_commission",
    # survivorship bias / point-in-time membership
    "Membership", "SurvivorshipConfig", "load_membership", "find_membership_file",
    "assert_no_look_ahead", "normalise", "DEFAULT_ALIAS", "DEFAULT_JUNK",
    # universe-wise reporting
    "UniverseReport",
    # reporting
    "Dashboard", "performance_stats", "drawdown_table",
    # the backtest dashboard: rendering engine + the panels that need no strategy.
    # The panels themselves live in qbeast_exec.dashboard - import them from there, since
    # exporting 12 panel_* names at top level would crowd the namespace for every caller.
    "__version__",
]
