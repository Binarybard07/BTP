r"""
qbeast_backtest/report.py
=========================
EVERYTHING A RUN LEAVES BEHIND. The pipeline prints a result table; this writes the files people
actually open.

    reports/<universe>/
        dashboard.html                 the fund dashboard - performance, risk, costs, regime,
                                       holdings, trades, config, audit
        equity.csv                     daily NAV (after tax), pre-tax NAV, invested, cash,
                                       deployment %, drawdown %
        summary.json                   the headline numbers, per universe, for the scenario matrix
        tradebook_backtest.csv         every FILL - date, symbol, side, qty, price, charges, slippage
        tradebook_by_symbol_*.csv      the same fills grouped by symbol
        orderbook_backtest.csv         every ORDER and its status        (Nautilus engine only)
        book_by_stock_backtest.csv     realised/unrealised P&L per name  (Nautilus engine only)
        book_summary_backtest.txt      the one-page book summary         (Nautilus engine only)
        nautilus_nav.csv               the canonical Nautilus NAV        (Nautilus engine only)
        staircase/                     flagged.csv, rebalance.csv, blocked.csv, episodes.csv, run.json

WHY THE ORDER BOOK IS NAUTILUS-ONLY, AND WHY THE TRADE BOOK IS NOT
------------------------------------------------------------------
`qbeast_exec.export_books` reads `engine.trader` - the Nautilus reports API - so there is no order
book without Nautilus: on the internal ledger no order object ever exists, only fills. Rather than
leave an internal run with no book at all, the trade book is written from `ledger.trades` here. That
is strictly more than Backtest_Core produces on the internal engine, and it means `--engine internal`
is still auditable fill by fill.

THE STATS KEY ALIASES
---------------------
`stats()` is `qbeast_exec.performance_stats` plus the short key names the dashboard's plotting code was
written against. The two sets carry the SAME VALUES (verified equal to 1e-9) under different names:

    maxdd = max_dd      yrs = years        avgdd = avg_dd       n_dd = n_dd_episodes
    best_yr = best_year worst_yr = worst_year  hit = hit_rate_monthly   total = total_return

One alias table beats forty renames through the figures, and it keeps the shared library as the single
definition of every number.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

# The short names the dashboard was written against -> the shared library's names. See the module note.
_ALIAS = {"maxdd": "max_dd", "yrs": "years", "avgdd": "avg_dd", "avgdd_ep": "avg_dd_episode",
          "n_dd": "n_dd_episodes", "best_yr": "best_year", "worst_yr": "worst_year",
          "hit": "hit_rate_monthly", "total": "total_return"}


def stats(nav: pd.Series) -> dict:
    """`qbeast_exec.performance_stats` with the dashboard's short key names added alongside."""
    from qbeast_exec import performance_stats
    s = dict(performance_stats(nav))
    for short, long in _ALIAS.items():
        if long in s:
            s.setdefault(short, s[long])
    return s


def _equity(r: dict) -> pd.DataFrame:
    """The daily curve, with the columns an operator asks for rather than only NAV.

    `cash` and `deployment` are what make an all-cash stretch legible: a flat NAV line alone cannot
    distinguish "held nothing" from "held things that did not move", and this strategy spends a large
    share of its life in cash by design."""
    nav = r["nav"]
    inv = r["invested"].reindex(nav.index).ffill().fillna(0.0)
    df = pd.DataFrame({"nav": nav, "nav_pretax": r["nav_pretax"].reindex(nav.index),
                       "invested": inv, "cash": (nav - inv).clip(lower=0),
                       "deployment_pct": (inv / nav * 100).clip(0, 100),
                       "drawdown_pct": (nav / nav.cummax() - 1.0) * 100,
                       "benchmark": r["bench"].reindex(nav.index)})
    df.index.name = "date"
    return df


def _tradebook(r: dict) -> pd.DataFrame:
    """Every fill, from the ledger. See the module note on why this is not Nautilus-only."""
    led = r.get("ledger")
    tr = getattr(led, "trades", None)
    if not tr:
        return pd.DataFrame()
    df = pd.DataFrame(tr)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
    ren = {"px": "price", "commission": "charges"}
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    if {"qty", "price"}.issubset(df.columns):
        df["notional"] = df["qty"].abs() * df["price"]
    return df.reset_index(drop=True)


def _by_symbol(tb: pd.DataFrame) -> pd.DataFrame:
    """Fills rolled up per name: how often it traded, what it cost, what it made."""
    if tb.empty or "symbol" not in tb.columns:
        return pd.DataFrame()
    agg = {c: "sum" for c in ("notional", "charges", "slippage", "realized", "pnl")
           if c in tb.columns}
    g = tb.groupby("symbol")
    out = g.agg(**{k: (k, v) for k, v in agg.items()}) if agg else pd.DataFrame(index=g.size().index)
    out.insert(0, "fills", g.size())
    if "qty" in tb.columns:
        out.insert(1, "bought", g["qty"].apply(lambda s: s[s > 0].sum()))
        out.insert(2, "sold", g["qty"].apply(lambda s: -s[s < 0].sum()))
    return out.sort_values("notional" if "notional" in out.columns else "fills", ascending=False)


def _frictions(r: dict) -> dict:
    """What the run paid, split the way the dashboard's cost tab reads it.

    The charges/slippage SPLIT is only meaningful on the internal ledger: the Nautilus fee model
    charges notional x (COST_PCT + slip) as ONE commission, so its `slippage` column reads 0 by
    construction and `commission` silently contains both. `cost_paid` is the authoritative total
    either way, so all three are recorded - reporting a zero slippage as if it were measured would be
    plainly wrong."""
    led = r.get("ledger")
    tb = _tradebook(r)
    out: dict = {}
    if not tb.empty:
        out = dict(charges=float(tb.get("charges", pd.Series(dtype=float)).sum()),
                   slippage=float(tb.get("slippage", pd.Series(dtype=float)).sum()),
                   turnover=float(tb["notional"].sum()) if "notional" in tb else 0.0,
                   fills=int(len(tb)))
    out.update(cost_paid=float(getattr(led, "cost_paid", 0.0)),
               tax=float(getattr(led, "tax_paid", 0.0)),
               interest=float(getattr(led, "interest", 0.0)))
    out["total_frictions"] = out["cost_paid"] + out["tax"]
    return out


def write(r: dict, outdir=None, *, dashboard: bool = True, verbose: bool = True) -> dict:
    """Write every report for a finished run. Returns {name: path} for what was written.

    `r` is `pipeline.run()`'s result. A failure in any one file is reported and the rest still get
    written: losing the dashboard to a plotting error must not also cost the equity curve and the
    trade book, which are the files a result gets checked against."""
    from qbeast_backtest import config as C

    out = Path(outdir or C.OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    written: dict = {}
    s, b = stats(r["nav"]), stats(r["bench"])
    s_pre = stats(r["nav_pretax"])
    eng = str(getattr(r.get("execution"), "engine", "") or C.EXEC_ENGINE).lower()

    def _try(name, fn):
        try:
            p = fn()
        except Exception as e:                                 # noqa: BLE001
            print(f"  ** {name} could not be written: {type(e).__name__}: {e} **", flush=True)
        else:
            if p is not None:
                written[name] = p

    # ---- the daily curve ------------------------------------------------------------------------
    def _eq():
        p = out / "equity.csv"
        _equity(r).to_csv(p)
        return p
    _try("equity.csv", _eq)

    # ---- the books ------------------------------------------------------------------------------
    tb = _tradebook(r)
    if not tb.empty:
        def _tb():
            p = out / "tradebook_backtest.csv"
            tb.to_csv(p, index=False)
            return p

        def _tbs():
            p = out / "tradebook_by_symbol_backtest.csv"
            _by_symbol(tb).to_csv(p)
            return p
        _try("tradebook_backtest.csv", _tb)
        _try("tradebook_by_symbol_backtest.csv", _tbs)
    elif verbose:
        print("  [report] no fills in this run - no trade book written "
              "(a window the gate held in cash is a legitimate outcome)", flush=True)

    # The ORDER book needs Nautilus - see the module note. On that engine stage 3 has already written
    # it (orderbook/tradebook/book_by_stock/book_summary, via qbeast_exec.export_books), so here we
    # only add the canonical NAV and report what is there.
    if eng == "nautilus":
        def _nn():
            p = out / "nautilus_nav.csv"
            r["nav"].to_frame("nav").to_csv(p)
            return p
        _try("nautilus_nav.csv", _nn)
        for f in ("orderbook_backtest.csv", "orderbook_by_symbol_backtest.csv",
                  "book_by_stock_backtest.csv", "book_summary_backtest.txt"):
            if (out / f).exists():
                written[f] = out / f

    # ---- the per-universe headline, for the scenario matrix -------------------------------------
    def _summary():
        from qbeast_exec import UniverseReport
        keep = ("cagr", "ann_arith", "simple", "maxdd", "avgdd", "avgdd_ep", "sharpe", "calmar",
                "final", "pct_underwater")
        rep = UniverseReport(C.REPORTS_ROOT, C.UNIVERSE)
        md = r.get("market")
        rep.save({k: s[k] for k in keep if k in s},
                 benchmark={k: b[k] for k in keep if k in b},
                 window=(r["nav"].index[0], r["nav"].index[-1]),
                 # `gate` was the removed strategy's trend gate. The strategy records ITS
                 # OWN settings instead, from StrategyOutput.params - so a stored result
                 # always carries the parameters that produced it, whatever strategy ran.
                 strategy_params=dict(getattr(r.get("strategy"), "params", None) or {}),
                 engine=eng, benchmark_name=str(C.INDEX_SYMBOL),
                 survivorship=bool(getattr(md, "survivorship", False)),
                 symbols=int(md.closes.shape[1]) if md is not None else None,
                 strategy=str(getattr(r.get("strategy"), "name", "") or ""),
                 cagr_pretax=float(s_pre["cagr"]), **_frictions(r))
        return rep.path if isinstance(getattr(rep, "path", None), Path) else out / "summary.json"
    _try("summary.json", _summary)

    # ---- the dashboard --------------------------------------------------------------------------
    if dashboard:
        def _dash():
            from qbeast_backtest import dashboard as D
            D.build(_dash_res(r, s, b, s_pre))
            return out / "dashboard.html"
        _try("dashboard.html", _dash)

    if verbose:
        print()
        print(f"  reports -> {out}")
        for k, v in written.items():
            try:
                kb = Path(v).stat().st_size / 1024
                print(f"    {k:<36} {kb:>10,.0f} KB")
            except OSError:
                print(f"    {k}")
        stair = r.get("staircase_log")
        if stair:
            print(f"    staircase/{'':<26} {', '.join(f'{k} {v}' for k, v in stair.items() if isinstance(v, int))}")
    return written


def _dash_res(r: dict, s: dict, b: dict, s_pre: dict) -> dict:
    """Adapt the pipeline's result to the keys `dashboard.build()` reads.

    The dashboard predates the stage contracts and expects a flat dict. Rather than rewrite 900 lines
    of plotting, the diagnostics it wants are lifted out of `StrategyOutput` here - which is where a
    strategy is supposed to publish them:

        trend_up / vol_regime   from out.diagnostics, the REINDEXED reporting frame (right for plots)
        index_close             md.signal_index, the series the gates actually read
        mom                     recomputed on history_closes, for the signal grid

    A strategy that reports no diagnostics still gets a dashboard; the regime tabs degrade to empty
    rather than the run failing, because a missing chart is not a wrong number."""
    md, out = r.get("market"), r.get("strategy")
    diag = getattr(out, "diagnostics", None)
    idx = r["nav"].index

    def _col(name, default):
        if diag is not None and name in getattr(diag, "columns", []):
            return diag[name].reindex(idx).ffill()
        return pd.Series(default, index=idx)

    d = dict(r)
    d.update(stats=s, stats_pretax=s_pre, bench_stats=b,
             trend_up=_col("trend_up", True).astype(bool),
             vol_regime=_col("vol_regime", "calm").astype(str),
             index_close=(md.signal_index if md is not None else r["bench"]),
             rebalance_log=_rebalance_log(out),
             memb=_EligibleView(md) if md is not None else None)
    # The dashboard's signal grid ranks the universe by the strategy's own signal. The reference
    # strategy's momentum went with it, so a strategy that wants that tab publishes `mom` in its
    # `diagnostics`; otherwise the grid is omitted. It is NOT recomputed here on a guess - a grid
    # ranked by a signal the strategy does not use is a chart of nothing, which is worse than no tab.
    if diag is not None and "mom" in getattr(diag, "columns", []):
        d["mom"] = diag["mom"]
    return d


def _rebalance_log(out) -> list[dict]:
    """The rebalance table, derived from the decisions themselves rather than logged alongside them.

    `StrategyOutput.targets` already IS the record of what was decided and when, so the log is a view
    of it - there is no second place for the two to disagree. `added` / `dropped` come from comparing
    consecutive decisions, and an EMPTY target book is a flatten, not a gap: it reports as posture
    "cash" with 0 names, which is the thing a reader needs to see on 41 of this run's 117 sessions."""
    if out is None or not getattr(out, "targets", None):
        return []
    expo = getattr(out, "exposure", None)
    post = getattr(out, "posture", None)
    log, prev = [], set()
    for t_ in sorted(out.targets):
        names = set(out.targets[t_])
        dep = float(expo.get(t_, 0.0)) if expo is not None else (1.0 if names else 0.0)
        if post is not None and t_ in post.index:
            posture = str(post.loc[t_])
        else:
            posture = "cash" if not names else ("full" if dep >= 0.999 else "sized")
        log.append(dict(date=pd.Timestamp(t_), posture=posture, deploy=dep, n=len(names),
                        added=len(names - prev), dropped=len(prev - names),
                        names=sorted(names)))
        prev = names
    return log


class _EligibleView:
    """`.eligible(date)` over the contract's published set, for the dashboard's signal grid.

    The dashboard was written against a `Membership` object. The eligible set stage 1 publishes is the
    better answer anyway: it is point-in-time membership ALREADY intersected with the staircase gate
    and tradability, so the grid cannot rank a name the run was forbidden to buy.

    `.asof` semantics, not exact-match: the grid asks about the last session in the panel, which is
    not usually a rebalance date, and an exact lookup would return nothing and silently widen the grid
    to the whole universe."""

    def __init__(self, md) -> None:
        self._md = md
        self._keys = sorted(md.eligible)

    def eligible(self, date) -> set:
        d = pd.Timestamp(date)
        prior = [k for k in self._keys if k <= d]
        if prior:
            return set(self._md.eligible[prior[-1]])
        return set(self._md.eligible[self._keys[0]]) if self._keys else set()
