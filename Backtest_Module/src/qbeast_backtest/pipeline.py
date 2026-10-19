r"""
Backtest_Modular/pipeline.py
============================
THE ONLY ORCHESTRATOR. Three calls, in order, each consuming the previous one's output:

    stage1_data.load_market_data()      ->  MarketData        prices, calendar, ELIGIBLE SET
    stage2_strategy.get(name).decide()  ->  StrategyOutput    what to hold, per rebalance
    stage3_execution.execute()          ->  ExecutionResult   fills, costs, tax, NAV

Each boundary is validated before the next stage runs, so a fault is reported where it was introduced
rather than three stages downstream as an implausible number.

USAGE
-----
    python pipeline.py                            # the configured universe + default strategy
    python pipeline.py --strategy my_strategy
    python pipeline.py --engine internal          # override config.EXEC_ENGINE for this call
    python pipeline.py --list                      # registered strategies
    python pipeline.py --no-staircase              # for an A/B of the gate's effect
    python pipeline.py --no-dashboard              # skip dashboard.html (the slow part)
    python pipeline.py --no-report                 # print the table only, write nothing

WHAT A RUN WRITES
-----------------
reports/<universe>/ gets dashboard.html, equity.csv, summary.json, the trade book (and the order book
plus nautilus_nav.csv on the Nautilus engine), and staircase/. See `report.py` for the full list.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The package directory goes on sys.path so the three stage packages import as FLAT modules -
# `import stage1_data`, `import stage2_strategy`, `import stage3_execution` - which is how they refer
# to each other and how a strategy author reads them in the docs.
#
# A second entry, `HERE / "shared"`, used to sit here with a comment saying it held the certified
# maths. It held nothing: the directory does not exist under the package, the maths lives in
# `qbeast_backtest` itself (signals.py, strategy.py, portfolio.py, data_io.py, engine.py), and the
# entry was left over from the pre-package layout. A path that is documented as load-bearing and is
# actually absent is worse than no comment, because the next person preserves it.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pandas as pd                                              # noqa: E402

from qbeast_backtest.contracts import ExecutionResult, MarketData, StrategyOutput  # noqa: E402,F401


def run(strategy: str | None = None, *, engine: str | None = None,
        verbose: bool = True, strict: bool = True) -> dict:
    """Wire the three stages and return everything a report needs."""
    import stage1_data
    import stage2_strategy
    import stage3_execution
    from stage1_data.loader import LAST_GATE

    from qbeast_backtest import config as C

    if verbose:
        print("=" * 100)
        print(f"STAGE 1 - DATA AND TRADABILITY   (universe {C.UNIVERSE}, benchmark {C.INDEX_SYMBOL})")
        print("=" * 100)
    md = stage1_data.load_market_data(verbose=verbose)
    probs = md.validate()
    if probs and strict:
        raise ValueError("stage 1 produced unusable data:\n  - " + "\n  - ".join(probs))
    gate = LAST_GATE[0]

    if verbose:
        print()
        print("=" * 100)
        print(f"STAGE 2 - STRATEGY   ({strategy or stage2_strategy.registry.DEFAULT})")
        print("=" * 100)
    strat = stage2_strategy.get(strategy)
    out = strat.decide(md)
    # The contract check. This is what makes the eligible set BINDING: a strategy that ranked outside
    # it - undoing the staircase gate or point-in-time membership - fails here, before any execution.
    probs = out.validate(md, cash_buffer=float(C.CASH_BUFFER))
    if verbose:
        n_t = len(out.targets)
        n_named = sum(1 for w in out.targets.values() if w)
        print(f"  [stage2] {strat.name}: {n_t} decision session(s), {n_named} holding a book, "
              f"{n_t - n_named} flattening to cash", flush=True)
        print(f"  [stage2] params: {out.params}", flush=True)
        for p in probs:
            print(f"  ** stage2 CONTRACT VIOLATION: {p} **", flush=True)
    if probs and strict:
        raise ValueError(f"strategy {strat.name!r} violated the stage-2 contract:\n  - "
                         + "\n  - ".join(probs))

    if verbose:
        print()
        print("=" * 100)
        print("STAGE 3 - EXECUTION, COSTS AND TAX   (qbeast_exec)")
        print("=" * 100)
    res = stage3_execution.execute(md, out, engine=engine,
                                   bands=getattr(gate, "bands", None),
                                   stair=getattr(gate, "staircase", None), verbose=verbose)
    probs = res.validate()
    if probs and strict:
        raise ValueError("stage 3 produced an unusable book:\n  - " + "\n  - ".join(probs))

    # THE STAIRCASE LOG - every run, every rebalance, every refused order. Written after stage 3 so
    # it can include the ledger backstop's refusals alongside the pool exclusions.
    stair_summary = None
    try:
        from stage1_data import staircase_log
        stair_summary = staircase_log.write(md, out, res, gate,
                                            getattr(C, "STAIRCASE_LOG_DIR", C.OUT_DIR / "staircase"))
        if verbose:
            print()
            print(staircase_log.console(stair_summary), flush=True)
    except Exception as e:                                     # noqa: BLE001 - an audit file must
        print(f"  ** staircase log could not be written: {type(e).__name__}: {e} **", flush=True)

    return dict(market=md, strategy=out, execution=res, gate=gate, staircase_log=stair_summary,
                # the keys the existing reporting code expects, so dashboards keep working
                nav=res.nav, nav_pretax=res.nav_pretax, invested=res.invested, bench=res.benchmark,
                ledger=res.ledger, cal=md.calendar, closes=md.closes, staircase=
                getattr(gate, "staircase", None), bands=getattr(gate, "bands", None))


def main() -> int:
    argv = sys.argv[1:]
    if "--list" in argv:
        import stage2_strategy
        print("registered strategies: " + ", ".join(stage2_strategy.available()))
        return 0

    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) \
            else default

    if "--no-staircase" in argv:
        from qbeast_backtest import config as C
        C.USE_STAIRCASE_FLAG = False
        print("  [pipeline] --no-staircase: the gate is OFF for this run (A/B comparison)", flush=True)

    r = run(strategy=opt("--strategy"), engine=opt("--engine"))

    # THE REPORTS. Written before the result table is printed, so a number on screen always has the
    # files behind it - printing the headline first and then failing to write the dashboard would
    # leave a result nobody can check. report.write() isolates each file's failure itself.
    if "--no-report" not in argv:
        from qbeast_backtest import report as _rep
        _rep.write(r, dashboard="--no-dashboard" not in argv)

    from qbeast_exec import performance_stats
    s = performance_stats(r["nav"])
    b = performance_stats(r["bench"])
    print()
    print("=" * 100)
    print(f"RESULT   {r['nav'].index[0].date()} .. {r['nav'].index[-1].date()}   "
          f"({s['years']:.2f} calendar years)")
    print("=" * 100)
    print(f"  {'':<22}{'strategy':>12}{'benchmark':>12}{'excess':>12}")
    for k, lab in (("cagr", "CAGR %"), ("ann_arith", "Ann. arithmetic %"),
                   ("simple", "Uncompounded %"), ("max_dd", "max drawdown %"),
                   ("sharpe", "Sharpe"), ("calmar", "Calmar")):
        sv, bv = s.get(k), b.get(k)
        if isinstance(sv, float) and isinstance(bv, float):
            print(f"  {lab:<22}{sv:>12.2f}{bv:>12.2f}{sv - bv:>12.2f}")
    print(f"\n  {r['market'].notes[0] if r['market'].notes else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
