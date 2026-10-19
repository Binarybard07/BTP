r"""
qbeast_exec/universe.py
-----------------------
Universe-wise reporting: give every universe its own output folder, then roll them up honestly.

WHY A MODULE
    A strategy is never run on one universe. It is run on large caps, then mid caps, then small caps,
    and the outputs land on top of each other in one `reports/` folder until nobody can say which
    dashboard belongs to which run. Worse, the summary numbers get compared directly -- a small-cap
    CAGR beside a large-cap CAGR, as though they answered the same question.

    This module fixes both: `UniverseReport` partitions the outputs, and `compare()` rolls them up with
    the benchmark-relative columns first, because THAT is the comparable number.

THE RULE IT ENFORCES
    Never quote an absolute return across universes as if comparable. A small-cap book in a small-cap
    decade and a large-cap book in a large-cap decade are two different questions. `compare()` therefore
    leads with excess-over-benchmark and drawdown, and labels the absolute columns as not comparable.

STANDALONE
    pandas + the standard library. No project imports. Use it from any strategy project.

QUICK START
    from qbeast_exec import UniverseReport

    rep = UniverseReport("reports", "smallcap250_ddq")
    nav.to_csv(rep.path("equity.csv"))                 # -> reports/smallcap250_ddq/equity.csv
    rep.save(stats, benchmark=bench_stats, window=(start, end), note="regime gate")

    print(UniverseReport.table("reports"))             # every universe, newest run each
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SUMMARY_NAME = "summary.json"

# What a rolled-up comparison shows, in order. Benchmark-relative first: those are the columns that
# mean the same thing in every universe.
COMPARE_COLUMNS = ["excess_cagr_pp", "excess_ann_arith_pp", "excess_simple_pp", "dd_better_pp", "calmar", "sharpe",
                   "cagr", "benchmark_cagr", "maxdd", "benchmark_maxdd", "final", "as_of"]

_NOT_COMPARABLE = {"cagr", "benchmark_cagr", "final"}


class UniverseReport:
    """One universe's output folder, plus the roll-up across all of them."""

    def __init__(self, root, universe: str, create: bool = True) -> None:
        self.root = Path(root)
        self.universe = str(universe).strip().lower()
        if not self.universe:
            raise ValueError("universe must be a non-empty name")
        self.dir = self.root / self.universe
        if create:
            self.dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- paths
    def path(self, *parts) -> Path:
        """A path inside this universe's folder, creating sub-directories as needed."""
        p = self.dir.joinpath(*[str(x) for x in parts])
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def __fspath__(self) -> str:                       # so open(rep, ...) and Path(rep) work
        return str(self.dir)

    def __repr__(self) -> str:
        return f"UniverseReport({str(self.root)!r}, {self.universe!r})"

    # ---------------------------------------------------------------- summary
    def save(self, stats: dict, benchmark: dict | None = None, window=None, **meta) -> Path:
        """Write this universe's headline to summary.json, for the roll-up to read.

        `stats` and `benchmark` are plain dicts -- typically cagr / maxdd / sharpe / calmar / final.
        Anything else you pass in `meta` (gate, engine, note, symbol count) is stored alongside and
        shown by `compare()` when you ask for it."""
        rec: dict = {"universe": self.universe,
                     "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                     **{k: _plain(v) for k, v in stats.items()}}
        if benchmark:
            rec.update({f"benchmark_{k}": _plain(v) for k, v in benchmark.items()})
            # Excess is computed PER RETURN MEASURE. A compounded excess and an uncompounded one are
            # different numbers, and pairing a strategy figure from one basis with an index figure from
            # the other is the easiest way to invent alpha.
            for _k, _name in (("cagr", "excess_cagr_pp"), ("ann_arith", "excess_ann_arith_pp"),
                              ("simple", "excess_simple_pp")):
                if _k in stats and _k in benchmark:
                    rec[_name] = round(float(stats[_k]) - float(benchmark[_k]), 4)
            if "maxdd" in stats and "maxdd" in benchmark:
                # positive = this book drew down LESS than its benchmark
                rec["dd_better_pp"] = round(abs(float(benchmark["maxdd"])) - abs(float(stats["maxdd"])), 4)
        if window is not None:
            rec["window_start"], rec["window_end"] = (str(_plain(w)) for w in window)
        rec.update({k: _plain(v) for k, v in meta.items()})
        out = self.path(SUMMARY_NAME)
        out.write_text(json.dumps(rec, indent=2, default=str), encoding="utf-8")
        return out

    def load(self) -> dict:
        """This universe's stored summary, or {} when it has never been run."""
        f = self.dir / SUMMARY_NAME
        if not f.exists():
            return {}
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    # ---------------------------------------------------------------- roll-up
    @staticmethod
    def universes(root) -> list[str]:
        """Every universe folder under `root` that holds a summary, newest first."""
        r = Path(root)
        if not r.exists():
            return []
        found = []
        for d in r.iterdir():                          # ONE pass: two passes can disagree on order
            f = d / SUMMARY_NAME
            if d.is_dir() and f.exists():
                found.append((f.stat().st_mtime, d.name))
        return [n for _, n in sorted(found, key=lambda x: -x[0])]

    @staticmethod
    def compare(root, universes=None, columns=None) -> pd.DataFrame:
        """One row per universe, benchmark-relative columns first.

        Universes with no stored summary are skipped rather than shown as blanks, so a missing run is
        visibly absent instead of looking like a zero."""
        names = list(universes) if universes is not None else UniverseReport.universes(root)
        rows = []
        for u in names:
            rec = UniverseReport(root, u, create=False).load()
            if rec:
                rows.append(rec)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).set_index("universe")
        cols = list(columns) if columns is not None else [c for c in COMPARE_COLUMNS if c in df.columns]
        rest = [c for c in df.columns if c not in cols]
        return df[cols + (rest if columns is None else [])]

    @staticmethod
    def table(root, universes=None, columns=None) -> str:
        """The comparison as text, with the caveat attached rather than assumed."""
        df = UniverseReport.compare(root, universes, columns)
        if df.empty:
            return f"no universe summaries under {root}"
        shown = [c for c in df.columns if c in set(COMPARE_COLUMNS)] or list(df.columns)
        body = df[shown].to_string(float_format=lambda x: f"{x:,.2f}")
        note = ("\n  excess_cagr_pp / dd_better_pp are benchmark-relative and ARE comparable across "
                "universes.\n  cagr / benchmark_cagr / final are NOT: each universe had a different "
                "decade.")
        return body + note


def _plain(v):
    """JSON-safe: timestamps to ISO dates, numpy scalars to python."""
    if isinstance(v, (pd.Timestamp, datetime)):
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            return v.item()
        except (ValueError, AttributeError):
            return v
    return v
