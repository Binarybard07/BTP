"""
qbeast_exec.isolation_audit — prove this copy of the library works in YOUR project.

    python -m qbeast_exec.isolation_audit          # quiet
    python -m qbeast_exec.isolation_audit -v       # with tracebacks

WHAT IT CHECKS, and why each matters

  1. NO REACH-BACK. Importing and exercising the library must not pull in a module from the project it
     came from. This is the failure that hurts most, because it is invisible at home: a library that
     quietly imports someone's `config.py` passes every test in its origin repo and dies on the first
     machine it is copied to. The check is not "is that module importable" (it may legitimately be on
     your path) but "did qbeast_exec IMPORT it" -- which is the thing that would break you.

  2. EVERY EXPORT RESOLVES from this copy alone.

  3. EVERY PUBLIC CALLABLE RUNS, with minimal valid inputs. An export that imports but raises on first
     call is not usable, and that distinction does not show up in an import test.

A clean result is `120 passed, 0 failed` with no reach-back. `nautilus_trader` is optional: if it is
absent the three instrument/bar helpers are skipped and reported as skipped, not passed.

Complements `python -m qbeast_exec.selftest`, which checks the ACCOUNTING (tax, charges, slippage,
drawdowns) rather than the packaging.
"""
from __future__ import annotations

from pathlib import Path as pathlib_Path

import sys
import traceback

PROJECT_MODULES = ("config", "engine", "signals", "strategy", "portfolio", "data_io", "universes",
                   "nautilus_run", "backtest", "regime_qbeast", "breaker", "slippage_model",
                   "membership", "run")

OK: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(name, fn):
    """Run `fn`; record pass/fail. A callable that raises is a failed export, not a warning."""
    try:
        fn()
        OK.append(name)
    except Exception as e:                                        # noqa: BLE001
        FAIL.append((name, f"{type(e).__name__}: {e}"))
        if "-v" in sys.argv:
            traceback.print_exc()


def reached_back() -> list[str]:
    """Project modules that appeared in sys.modules AFTER qbeast_exec was imported."""
    return sorted(m for m in PROJECT_MODULES if m in sys.modules and m not in _before)


# ============================================================ 0. environment
print("qbeast_exec ISOLATION AUDIT")
print("=" * 78)
print(f"  running from   : {pathlib_Path.cwd()}")

# Snapshot sys.modules BEFORE the library is imported. Anything from PROJECT_MODULES that appears
# afterwards was pulled in BY qbeast_exec -- which is the actual defect. Whether those modules happen
# to be importable from here is irrelevant and varies by cwd.
_before = set(sys.modules)

import numpy as np
import pandas as pd

import qbeast_exec as q

print(f"\n  qbeast_exec {q.__version__}, {len(q.__all__)} exports")
print(f"  reach-back after import: {reached_back() or 'NONE  <- good'}")

# ============================================================ shared fixtures
IDX = pd.to_datetime([f"2024-01-{d:02d}" for d in range(1, 11)])
CLOSES = pd.DataFrame({"AAA": np.linspace(100, 120, 10), "BBB": np.linspace(50, 45, 10)}, index=IDX)
OPENS = CLOSES.shift(1).bfill()
HIGH = CLOSES * 1.02
LOW = CLOSES * 0.98
OHLCV = pd.DataFrame({"open": OPENS["AAA"], "high": HIGH["AAA"], "low": LOW["AAA"],
                      "close": CLOSES["AAA"], "volume": 100_000.0}, index=IDX)
NAV = pd.Series(np.linspace(1_000_000, 1_250_000, 260) * (1 + 0.02 * np.sin(np.arange(260) / 7)),
                index=pd.bdate_range("2024-01-01", periods=260))

# ============================================================ 1. tax
print("\n--- 1. tax ---")
from qbeast_exec import Lot, LotClose, TaxConfig, TaxEngine

tc = TaxConfig()
te = TaxEngine(tc)
check("TaxConfig()", lambda: TaxConfig())
check("TaxConfig.is_long_term", lambda: tc.is_long_term(pd.Timestamp("2023-01-01"),
                                                        pd.Timestamp("2024-06-01")))
check("TaxConfig.fy_start_month", lambda: tc.fy_start_month)
check("TaxEngine.buy", lambda: te.buy("AAA", IDX[0], 100, 100.0))
check("TaxEngine.sell", lambda: te.sell("AAA", IDX[5], 50, 110.0))
check("TaxEngine.assess", lambda: te.assess(pd.Timestamp("2025-04-01")))
check("TaxEngine.assess_all", lambda: te.assess_all())
check("TaxEngine.closed_lots", lambda: te.closed_lots())
check("TaxEngine.holding_period_summary", lambda: te.holding_period_summary())
check("TaxEngine.fy", lambda: te.fy(IDX[0]))
check("TaxEngine.fy_end", lambda: te.fy_end(2024))
check("TaxEngine.fy_label", lambda: te.fy_label(2024))
check("TaxEngine.apply_fills", lambda: TaxEngine(tc).apply_fills(
    [dict(date=IDX[0], symbol="AAA", qty=10, price=100.0)]))
check("Lot / LotClose dataclasses", lambda: (Lot, LotClose))

# ============================================================ 2. charges + slippage
print("--- 2. charges + slippage ---")
from qbeast_exec import (ChargesCalculator, ChargesConfig, NautilusFeeModel, NautilusFillModel,
                         SlippageConfig, SlippageModel, atr_pct, corwin_schultz)

cc = ChargesConfig.equity_delivery()
calc = ChargesCalculator(cc)
check("ChargesConfig.equity_delivery", lambda: ChargesConfig.equity_delivery())
check("ChargesConfig.equity_intraday", lambda: ChargesConfig.equity_intraday())
check("ChargesConfig.blended", lambda: ChargesConfig.blended(0.0020))
check("ChargesConfig.futures", lambda: ChargesConfig.futures())
check("ChargesConfig.options", lambda: ChargesConfig.options())
check("ChargesCalculator.total", lambda: calc.total("BUY", 100, 100.0))
check("ChargesCalculator.breakdown", lambda: calc.breakdown("SELL", 100, 100.0))
check("ChargesCalculator.schedule", lambda: calc.schedule())
check("ChargesCalculator.effective_rate", lambda: calc.effective_rate("BUY", 100, 100.0))

frames = {"AAA": OHLCV}
sm = SlippageModel(frames, SlippageConfig())
check("SlippageConfig()", lambda: SlippageConfig())
check("SlippageModel(frames)", lambda: SlippageModel(frames))
check("SlippageModel.terms", lambda: sm.terms("AAA", IDX[5], 100, 110.0))
check("SlippageModel.fill_price", lambda: sm.fill_price("AAA", IDX[5], "BUY", 100, 110.0))
check("SlippageModel.draw", lambda: sm.draw("AAA", IDX[5], "BUY", 100, 110.0))
check("SlippageModel.capacity", lambda: sm.capacity(["AAA"], IDX[5], [1_000_000.0], top_n=1))
check("SlippageModel.reference_price", lambda: sm.reference_price("AAA", IDX[5], 110.0))
check("SlippageModel.summary", lambda: sm.summary())
check("SlippageModel.realised", lambda: sm.realised())
check("corwin_schultz", lambda: corwin_schultz(HIGH["AAA"], LOW["AAA"], window=5))
check("atr_pct", lambda: atr_pct(OHLCV, n=5))
check("NautilusFeeModel(cfg)", lambda: NautilusFeeModel(cc))
check("NautilusFillModel(sm)", lambda: NautilusFillModel(sm))

# ============================================================ 3. bands / staircase / MWCB
print("--- 3. bands / staircase / MWCB ---")
from qbeast_exec import (BandConfig, MarketWideBreaker, MWCBConfig, PriceBandModel,
                         StaircaseConfig, StaircaseFlag)

pb = PriceBandModel(frames, BandConfig())
check("BandConfig()", lambda: BandConfig())
check("PriceBandModel.check", lambda: pb.check("AAA", IDX[5], "BUY", 10))
check("PriceBandModel.can_trade", lambda: pb.can_trade("AAA", IDX[5], "BUY"))
check("PriceBandModel.state", lambda: pb.state("AAA", IDX[5]))
check("PriceBandModel.band", lambda: pb.band("AAA", IDX[5]))
check("PriceBandModel.infer_band", lambda: pb.infer_band(CLOSES["AAA"].pct_change()))
check("PriceBandModel.scan", lambda: pb.scan())
check("PriceBandModel.clamp", lambda: pb.clamp("AAA", 115.0, 110.0, IDX[5]))
check("PriceBandModel.bands_table", lambda: pb.bands_table())
check("PriceBandModel.blocked_table", lambda: pb.blocked_table())
check("PriceBandModel.summary", lambda: pb.summary())

sf = StaircaseFlag(frames, StaircaseConfig())
check("StaircaseConfig()", lambda: StaircaseConfig())
check("StaircaseFlag.flagged", lambda: sf.flagged("AAA", IDX[5]))
check("StaircaseFlag.steps", lambda: sf.steps("AAA", IDX[5]))
check("StaircaseFlag.check", lambda: sf.check("AAA", IDX[5], "BUY", 10))
check("StaircaseFlag.can_trade", lambda: sf.can_trade("AAA", IDX[5], "BUY"))
check("StaircaseFlag.flagged_symbols", lambda: sf.flagged_symbols(IDX[5]))
check("StaircaseFlag.table", lambda: sf.table(IDX[5]))
check("StaircaseFlag.blocked_table", lambda: sf.blocked_table())
check("StaircaseFlag.summary", lambda: sf.summary())
check("StaircaseFlag.report", lambda: sf.report(IDX[5]))
check("StaircaseFlag.scan", lambda: sf.scan())

mw = MarketWideBreaker(CLOSES["AAA"], MWCBConfig())
check("MWCBConfig()", lambda: MWCBConfig())
check("MarketWideBreaker.state", lambda: mw.state(IDX[5]))
check("MarketWideBreaker.scan", lambda: mw.scan())
check("MarketWideBreaker.stress", lambda: mw.stress(IDX[5], -0.11))
check("MarketWideBreaker.clear_stress", lambda: mw.clear_stress())
check("MarketWideBreaker.reopen_gap", lambda: mw.reopen_gap(IDX[5], -0.11))
check("MarketWideBreaker.near_misses", lambda: mw.near_misses())
check("MarketWideBreaker.halt_minutes", lambda: mw.halt_minutes(IDX[5]))
check("MarketWideBreaker.summary", lambda: mw.summary())
check("MarketWideBreaker.report", lambda: mw.report())

# ============================================================ 4. survivorship
print("--- 4. survivorship ---")
from qbeast_exec import (DEFAULT_ALIAS, DEFAULT_JUNK, Membership, SurvivorshipConfig,
                         assert_no_look_ahead, find_membership_file, load_membership, normalise)

mb = Membership(available={"AAA", "BBB"})
check("SurvivorshipConfig()", lambda: SurvivorshipConfig())
check("Membership(available=)", lambda: Membership(available={"AAA"}))
check("Membership.eligible", lambda: mb.eligible(IDX[5]))
check("Membership.members", lambda: mb.members(IDX[5]))
check("Membership.audit", lambda: mb.audit())
check("Membership.coverage", lambda: mb.coverage())
check("Membership.missing", lambda: mb.missing())
check("Membership.report", lambda: mb.report())
check("Membership.first_date", lambda: mb.first_date)
check("Membership.enabled", lambda: mb.enabled)
check("normalise", lambda: normalise(" aaa-eq "))
check("find_membership_file", lambda: find_membership_file("nope_does_not_exist"))
check("assert_no_look_ahead", lambda: assert_no_look_ahead(mb, IDX[0]))
check("DEFAULT_ALIAS / DEFAULT_JUNK", lambda: (len(DEFAULT_ALIAS), len(DEFAULT_JUNK)))
check("load_membership(missing, non-strict)",
      lambda: load_membership("nope.csv", SurvivorshipConfig(strict=False)))

# ============================================================ 5. universe reporting
print("--- 5. universe reporting ---")
import tempfile
from pathlib import Path

from qbeast_exec import UniverseReport

root = Path(tempfile.mkdtemp())
ur = UniverseReport(root, "TestUniverse")
check("UniverseReport(root, name)", lambda: UniverseReport(root, "X"))
check("UniverseReport.path", lambda: ur.path("sub", "f.csv"))
check("UniverseReport.save", lambda: ur.save({"cagr": 14.0, "maxdd": -20.0},
                                             benchmark={"cagr": 10.0, "maxdd": -40.0}))
check("UniverseReport.load", lambda: ur.load())
check("UniverseReport.universes", lambda: UniverseReport.universes(root))
check("UniverseReport.compare", lambda: UniverseReport.compare(root))
check("UniverseReport.table", lambda: UniverseReport.table(root))

# ============================================================ 6. reporting
print("--- 6. reporting ---")
from qbeast_exec import Dashboard, drawdown_table, performance_stats

check("performance_stats", lambda: performance_stats(NAV))
check("drawdown_table", lambda: drawdown_table(NAV, top=3))
# Dashboard is a DATACLASS, not a builder: Dashboard(nav, benchmark=, tax=, slippage=, charges=,
# trades=, title=, subtitle=, periods_per_year=) with exactly three methods. `title` and the rest are
# FIELDS -- calling dash.title(...) raises "'str' object is not callable".
dash = Dashboard(NAV, benchmark=NAV * 0.9, title="Audit run", subtitle="isolation audit",
                 trades=pd.DataFrame([dict(date=IDX[1], symbol="AAA", qty=100, px=101.0, cost=25.0)]),
                 charges=calc.breakdown("BUY", 100, 100.0), slippage=sm.summary(),
                 tax=te.assess_all())
check("Dashboard(nav, ...)", lambda: Dashboard(NAV))
check("Dashboard.stats", lambda: dash.stats())
check("Dashboard.to_html", lambda: dash.to_html())
check("Dashboard.write", lambda: dash.write(root / "dashboard.html"))

# ============================================================ 7. ledger + T->T+1
print("--- 7. ledger + T->T+1 contract ---")
from qbeast_exec import (DeferredSubmit, Ledger, aftertax_nav, apply_fill, make_bars, make_equity,
                         replay_fills, split_commission)

led = Ledger(1_000_000, tax_config=tc, charges_config=cc, cash_yield=0.04)
check("Ledger(...)", lambda: Ledger(1_000_000))
check("Ledger.mark", lambda: led.mark(IDX[0], CLOSES.loc[IDX[0]]))
check("Ledger.nav_at", lambda: led.nav_at(CLOSES.loc[IDX[0]]))
check("Ledger.equity_curve", lambda: led.equity_curve())
check("Ledger.lots", lambda: led.lots)
check("Ledger.realized", lambda: led.realized)
check("Ledger.assessed", lambda: led.assessed)
check("Ledger.lot_closes", lambda: led.lot_closes)
check("apply_fill", lambda: apply_fill(led, IDX[1], "AAA", 100, 101.0, 25.0))
check("split_commission", lambda: split_commission(calc, 100, 101.0, 25.0))

FILLS = [dict(ts=IDX[1], symbol="AAA", qty=100, px=101.0, comm=25.0),
         dict(ts=IDX[5], symbol="AAA", qty=-100, px=112.0, comm=25.0)]
check("replay_fills (net)", lambda: replay_fills(FILLS, CLOSES, IDX, 1_000_000, verbose=False))
check("replay_fills (gross)",
      lambda: replay_fills(FILLS, CLOSES, IDX, 1_000_000, tax=False, verbose=False))
check("replay_fills (opens= reprice)",
      lambda: replay_fills(FILLS, CLOSES, IDX, 1_000_000, opens=OPENS, verbose=False))
check("aftertax_nav", lambda: aftertax_nav(FILLS, CLOSES, IDX, 1_000_000, verbose=False))

d = DeferredSubmit()
check("DeferredSubmit()", lambda: DeferredSubmit())
check("DeferredSubmit.queue", lambda: d.queue(IDX[0], {"AAA": 1.0}))
check("DeferredSubmit.waiting", lambda: d.waiting)
check("DeferredSubmit.release", lambda: list(d.release()))
check("DeferredSubmit.released", lambda: d.released)

# nautilus_trader is optional; report rather than fail if absent
try:
    import nautilus_trader                                         # noqa: F401
    HAS_NT = True
except ImportError:
    HAS_NT = False
if HAS_NT:
    inst = make_equity("AAA", venue="XNSE")
    check("make_equity", lambda: make_equity("AAA", venue="XNSE"))
    check("make_bars (offset_ns=0)",
          lambda: make_bars(inst, OPENS["AAA"], HIGH["AAA"], LOW["AAA"], CLOSES["AAA"], None, 0))
    check("make_bars (offset_ns=1)",
          lambda: make_bars(inst, OPENS["AAA"], HIGH["AAA"], LOW["AAA"], CLOSES["AAA"], None, 1))
    check("make_bars: 1ns ordering holds", lambda: (
        make_bars(inst, OPENS["AAA"], None, None, CLOSES["AAA"], None, 1)[0].ts_event
        - make_bars(inst, OPENS["AAA"], None, None, CLOSES["AAA"], None, 0)[0].ts_event == 1)
        or (_ for _ in ()).throw(AssertionError("offset not 1ns")))
else:
    print("    (nautilus_trader absent -- make_equity/make_bars/export_books skipped)")

check("export_books importable", lambda: q.export_books)

# ============================================================ verdict
print("\n" + "=" * 78)
back = reached_back()
print(f"  reach-back into project modules : {back or 'NONE'}")
print(f"  callables exercised             : {len(OK) + len(FAIL)}")
print(f"  passed                          : {len(OK)}")
print(f"  failed                          : {len(FAIL)}")
if FAIL:
    print("\n  FAILURES:")
    for n, e in FAIL:
        print(f"    {n:42} {e}")
clean = not FAIL and not back
print(f"\nVERDICT: {'SELF-CONTAINED' if clean else 'NOT CLEAN -- see above'}")
sys.exit(0 if clean else 1)
