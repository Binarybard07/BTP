r"""
Backtest_Modular/reconcile.py
=============================
THE ACCEPTANCE TEST FOR THE REFACTOR. A re-layered backtest that cannot reproduce the one it was
derived from is not a refactor, it is a new and unvalidated strategy wearing the old name.

It runs the same window, the same config and the same engine twice:

    A   Backtest_Core/backtest.run(engine)             the monolith
    B   Backtest_Modular/pipeline.run(engine)          the three modules

and compares the NAV series bar by bar.

TWO ARMS, because there are two different questions:

    --identical   staircase pool-exclusion OFF  ->  B must equal A to the rupee.
                  This isolates the REFACTOR. Any difference here is a bug I introduced while moving
                  code, and nothing else in this folder can be believed until it is zero.

    --effect      staircase pool-exclusion ON   ->  B is EXPECTED to differ from A.
                  This isolates the CHANGE: what using the gate in selection actually costs or earns.
                  Reported, never asserted - the number is the finding, not a pass condition.

Conflating those two is how a refactor bug gets filed as a strategy improvement.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent                     # src/qbeast_backtest
PROJ = HERE.parent.parent                                  # the project root
# OPTIONAL. This package runs with Backtest_Core absent; only this one comparison tool wants it.
# $QBEAST_BACKTEST_CORE overrides, so a checkout anywhere can be compared against.
import os as _os
CORE = Path(_os.environ.get("QBEAST_BACKTEST_CORE") or (PROJ.parent / "Backtest_Core"))
PY = str(CORE / ".venv" / "Scripts" / "python.exe")
if not Path(PY).exists():
    PY = sys.executable                                    # fall back to whatever is running us

# Each arm runs in its OWN interpreter. Both folders expose flat modules called `config`, `signals`,
# `strategy`, ... so importing one poisons sys.modules for the other: the second import is a no-op and
# the "comparison" silently runs the same code twice and reports a perfect match. Subprocesses are the
# only honest way to compare two copies of the same module names.
_ARM = r'''
import json, os, sys, pathlib
root = pathlib.Path(sys.argv[1])
engine = sys.argv[2]
mode = sys.argv[3]                      # "core" | "modular"
stair_pool = sys.argv[4] == "1"

# FORCE the settings that must match, rather than trusting two config.py files to agree. They did
# not: the first run of this script found arm A on weighting "equal" and arm B on "invvol", because the
# two folders' configs had drifted while the research config was being edited. A comparison between
# two different strategies tells you nothing, so the guard refused it - and the fix is to pin them
# here rather than to re-notice the drift every time.
FORCE = json.loads(sys.argv[5]) if len(sys.argv) > 5 else {}
FORCE_ENV = json.loads(sys.argv[6]) if len(sys.argv) > 6 else {}

# NOTE FOR ANYONE EDITING THIS STRING: it is SOURCE CODE INSIDE A STRING LITERAL, and the two arms
# import DIFFERENT modules that happen to share a name. The "core" arm must import Backtest_Core's
# FLAT `config`; the "modular" arm must import `qbeast_backtest.config`. A bulk import-rewrite once
# changed both to the package form, so the core arm silently read the MODULAR config - and the two
# arms then reported identical settings no matter how far apart they really were, which defeated the
# mismatch guard entirely. Keep the two branches distinct and do not let a tool rewrite them.
if mode == "core":
    sys.path.insert(0, str(root))
    import config as C                              # Backtest_Core's own flat module
else:
    # ADOPT Core's universe and window BEFORE importing config: DATA_DIR, OUT_DIR, the membership file
    # and the benchmark are all derived from UNIVERSE at import, so a later setattr leaves them stale.
    for _e in ("QBEAST_BACKTEST_UNIVERSE", "QBEAST_BACKTEST_START", "QBEAST_BACKTEST_END"):
        if _e in FORCE_ENV:
            os.environ[_e] = str(FORCE_ENV[_e])
    sys.path.insert(0, str(root / "src"))
    from qbeast_backtest import config as C

for _k, _v in FORCE.items():
    setattr(C, _k, _v)
C.USE_STAIRCASE_FLAG = True             # the gate is BUILT in both arms

if mode == "core":
    import backtest                     # the monolith: gate consulted at fill time only, as shipped
    r = backtest.run(engine=engine)
else:
    C.STAIRCASE_EXCLUDE_FROM_POOL = stair_pool
    from qbeast_backtest import pipeline
    r = pipeline.run(engine=engine, verbose=False)
nav = r["nav"]

out = dict(index=[str(d.date()) for d in nav.index], nav=[float(x) for x in nav.values],
           universe=str(C.UNIVERSE), start=str(C.BACKTEST_START.date()),
           end=str(C.BACKTEST_END.date()), sessions=int(len(nav)),
           data_dir=pathlib.Path(str(C.DATA_DIR)).name,
           weighting=str(C.WEIGHTING), engine=engine,
           top_n=int(C.TOP_N))
print("@@JSON@@" + json.dumps(out))
'''


# DD_EXIT and friends are pinned too: the drawdown breaker flattens the whole book, so leaving it
# unpinned lets one arm trade a breaker the other does not and the comparison measures that instead.
# PIN EVERY SHARED SCALAR SETTING, rather than a hand-maintained list of "the ones that matter".
#
# The curated list leaked three times: WEIGHTING, then DD_*, then CHARGES_MODE (Core on "blended"
# against this copy's "itemised", worth 0.1% of terminal NAV). Each was found only because the
# comparison failed and somebody diffed the configs by hand. A curated list is a list of the settings
# someone thought of.
#
# EXCLUDED deliberately:
#   path-like        derived from UNIVERSE, and legitimately different between two checkouts
#   UNIVERSE/window  adopted through the env overrides instead, because DATA_DIR and the membership
#                    file are derived from them AT IMPORT and cannot be set afterwards
#   the staircase    its whole point is to differ between the arms; each arm sets it explicitly
_EXCLUDE = {"UNIVERSE", "BACKTEST_START", "BACKTEST_END", "USE_STAIRCASE_FLAG",
            "STAIRCASE_EXCLUDE_FROM_POOL", "STAIRCASE_LOG_DIR"}
_PATHISH = ("DIR", "ROOT", "FILE", "SOURCE", "PATH")

# One line, no embedded newlines: an `isinstance` filter instead of try/except around json.dumps, so
# this survives being passed through a shell as `python -c`.
_READ = (
    "import sys, json; sys.path.insert(0, sys.argv[1]); import config as C; "
    "EX = set(sys.argv[2].split(',')); PA = tuple(sys.argv[3].split(',')); "
    "ks = [k for k in dir(C) if k.isupper() and not k.startswith('_') and k not in EX "
    "      and not any(p in k for p in PA)]; "
    "d = {k: getattr(C, k) for k in ks}; "
    "d = {k: v for k, v in d.items() "
    "     if v is None or isinstance(v, (bool, int, float, str))}; "
    "d['__env__'] = {'QBEAST_BACKTEST_UNIVERSE': str(C.UNIVERSE), "
    "                'QBEAST_BACKTEST_START': str(C.BACKTEST_START.date()), "
    "                'QBEAST_BACKTEST_END': str(C.BACKTEST_END.date())}; "
    "print('@@S@@' + json.dumps(d))"
)


def _reference_settings() -> dict:
    """The settings BOTH arms are pinned to, read from Backtest_Core.

    Backtest_Core is the reference - the question is whether the three modules reproduce IT - so its
    config defines the comparison and the modular copy is overridden to match."""
    r = subprocess.run([PY, "-c", _READ, str(CORE), ",".join(sorted(_EXCLUDE)),
                        ",".join(_PATHISH)], capture_output=True, text=True, cwd=str(CORE))
    for line in (r.stdout or "").splitlines():
        if line.startswith("@@S@@"):
            return json.loads(line[5:])
    raise RuntimeError("could not read the reference settings from Backtest_Core:\n"
                       + (r.stderr or r.stdout or "")[-400:])


def _run_arm(root: Path, engine: str, mode: str, stair_pool: bool, force: dict,
             force_env: dict | None = None) -> dict:
    r = subprocess.run([PY, "-c", _ARM, str(root), engine, mode, "1" if stair_pool else "0",
                        json.dumps(force), json.dumps(force_env or {})],
                       capture_output=True, text=True, cwd=str(root))
    for line in (r.stdout or "").splitlines():
        if line.startswith("@@JSON@@"):
            return json.loads(line[len("@@JSON@@"):])
    tail = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-12:])
    raise RuntimeError(f"{mode} arm produced no result (exit {r.returncode}):\n{tail}")


def compare(engine: str = "internal", stair_pool: bool = False) -> int:
    import pandas as pd

    # The research config has ONE owner. Pull it first: this script refused three comparisons in a
    # row because the two copies had drifted (WEIGHTING, then UNIVERSE), and a comparison between two
    # configurations measures the configurations, not the code.
    if not CORE.exists():
        print(f"Backtest_Core not found at {CORE} - nothing to compare against.")
        print("This package does not need it to run; set $QBEAST_BACKTEST_CORE to a checkout if you "
              "want the comparison.")
        return 0
    # The two configs are INDEPENDENT now and will drift - intended, since this package is meant to be
    # installed and run on its own. So the comparison PINS the settings it cares about to
    # Backtest_Core's values rather than assuming the two files agree. Three comparisons were refused
    # for exactly that reason before this existed (WEIGHTING, then UNIVERSE).
    force = _reference_settings()
    env = force.pop("__env__", {})
    # TREND_GATE and TARGET_VOL went with the removed strategy.
    _show = ("TOP_N", "WEIGHTING", "CHARGES_MODE", "SLIPPAGE_MODE", "DD_EXIT",
             "POINT_IN_TIME", "USE_PRICE_BANDS", "CASH_BUFFER")
    print(f"pinned {len(force)} shared setting(s) from Backtest_Core; the ones that move a number: "
          + ", ".join(f"{k}={force[k]}" for k in _show if k in force))
    print(f"arm A  Backtest_Core      engine={engine}  (staircase at fill time, as shipped)", flush=True)
    print("arm B adopts Core's universe/window: "
          + ", ".join(f"{k.rsplit('_', 1)[-1].lower()}={v}" for k, v in env.items()))
    a = _run_arm(CORE, engine, "core", False, force)
    print(f"arm B  Backtest_Modular   engine={engine}  "
          f"staircase pool-exclusion {'ON' if stair_pool else 'OFF'}", flush=True)
    b = _run_arm(HERE, engine, "modular", stair_pool, force, env)

    # The CALENDAR and the DATA FOLDER are the real check. Two arms can agree on every scalar setting
    # and still read different data - which is exactly how a 76% NAV gap got reported as a refactor
    # failure when the arms were on different universes over different windows.
    for k in ("universe", "start", "end", "data_dir", "weighting", "top_n", "gate", "sessions"):
        if a[k] != b[k]:
            print(f"  ** the two arms did not run the same configuration: {k} {a[k]!r} vs {b[k]!r} - "
                  f"the comparison is meaningless **")
            return 2
    print(f"\nboth arms: universe {a['universe']}, from {a['start']}, weighting {a['weighting']}, "
          f"top-{a['top_n']}, gate {a['gate']}")

    na = pd.Series(a["nav"], index=pd.to_datetime(a["index"]))
    nb = pd.Series(b["nav"], index=pd.to_datetime(b["index"]))
    print(f"  A: {len(na)} sessions  final Rs {na.iloc[-1]:,.2f}")
    print(f"  B: {len(nb)} sessions  final Rs {nb.iloc[-1]:,.2f}")

    common = na.index.intersection(nb.index)
    if len(common) != len(na) or len(common) != len(nb):
        print(f"  ** calendars differ: {len(na)} vs {len(nb)} sessions, {len(common)} shared **")
    d = (nb.reindex(common) - na.reindex(common)).abs()
    rel = d / na.reindex(common).abs()
    print(f"\n  max |difference|      Rs {d.max():,.4f}   ({rel.max() * 100:.6f}% of NAV)")
    print(f"  sessions differing    {int((d > 0.005).sum())} of {len(common)}")
    print(f"  final NAV difference  Rs {nb.iloc[-1] - na.iloc[-1]:,.2f} "
          f"({(nb.iloc[-1] / na.iloc[-1] - 1) * 100:+.4f}%)")

    if not stair_pool:
        # THE REFACTOR ARM. Rs 0.01 of slack for float accumulation over thousands of sessions; nothing
        # structural hides under a paisa.
        ok = bool(d.max() <= 0.01)
        print(f"\n  VERDICT: {'PASS - the refactor is faithful' if ok else 'FAIL - the three modules do NOT reproduce the monolith'}")
        if not ok:
            worst = d.nlargest(5)
            print("  worst sessions:")
            for t, v in worst.items():
                print(f"    {t.date()}  A {na[t]:>14,.2f}   B {nb[t]:>14,.2f}   diff {v:>12,.2f}")
        return 0 if ok else 1

    # THE EFFECT ARM. A difference is the finding, not a failure.
    from qbeast_exec import performance_stats
    sa, sb = performance_stats(na), performance_stats(nb)
    print(f"\n  THE STAIRCASE GATE'S EFFECT ON SELECTION (not a pass/fail):")
    print(f"  {'':<22}{'fill-time only':>16}{'pool exclusion':>16}{'change':>12}")
    for k, lab in (("cagr", "CAGR %"), ("max_dd", "max drawdown %"), ("sharpe", "Sharpe"),
                   ("calmar", "Calmar"), ("final", "final Rs")):
        va, vb = sa.get(k), sb.get(k)
        if isinstance(va, float) and isinstance(vb, float):
            print(f"  {lab:<22}{va:>16,.2f}{vb:>16,.2f}{vb - va:>12,.2f}")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    engine = argv[argv.index("--engine") + 1] if "--engine" in argv else "internal"
    if "--effect" in argv:
        return compare(engine, stair_pool=True)
    return compare(engine, stair_pool=False)


if __name__ == "__main__":
    sys.exit(main())
