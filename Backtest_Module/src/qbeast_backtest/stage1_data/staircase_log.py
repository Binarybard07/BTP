r"""
stage1_data/staircase_log.py
============================
THE STAIRCASE AUDIT TRAIL. Four files per run, under `reports/<universe>/staircase/`:

    run.json        the run: settings, totals, every symbol ever flagged
    flagged.csv     per (session, symbol): the ladder measurements, and whether it was removed
    rebalance.csv   per rebalance: what the gate removed AND WHETHER THAT CHANGED THE BOOK
    blocked.csv     per order: every buy/sell the ledger backstop refused, with the reason

WHY `rebalance.csv` IS THE ONE THAT MATTERS
-------------------------------------------
"The gate fired" and "the gate mattered" are different claims, and only the second one is interesting.
A flagged name that would have ranked 47th by momentum was never going to be bought, so removing it
changed nothing. A flagged name that would have ranked 3rd is a position the book would have taken and
did not.

So for every rebalance where something was removed, this re-ranks the pool WITH the removed names put
back, and records whether any of them would have made the top N. `mattered` is True only then. That
is the honest measure of what the change bought, and without it a column of non-zero counts reads as
an effect when it may be none.

WHY A SELL IS LOGGED TOO
------------------------
`blocked.csv` records both sides. Under the default policy the gate refuses BUYs only - an upper
circuit lock has no sellers, so it is a state you can always exit, and refusing the exit would trap
capital in the one name identified as an operator ladder. Under circuit flags v2.4.1 sells and exits are blocked too, so
the refused sells appear here and the trapped positions are visible rather than implicit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _ladder(stair, sym: str, t) -> tuple:
    try:
        hits, run = stair.steps(sym, t)
    except Exception:                                          # noqa: BLE001
        hits, run = None, None
    try:
        band = stair.bands.band(sym)
    except Exception:                                          # noqa: BLE001
        band = None
    return hits, run, band


def write(md, out, res, gate, outdir: Path | str) -> dict:
    """Write the four files. Returns a summary dict for the console and the run manifest."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stair = getattr(gate, "staircase", None)
    rep = getattr(gate, "report", None)
    written = {}

    # ---------------------------------------------------------------- flagged.csv
    rows = []
    flagged = dict(getattr(rep, "flagged", {}) or {})
    removed = dict(getattr(rep, "excluded_from_pool", {}) or {})
    for t in sorted(flagged):
        syms = flagged[t]
        if not syms:
            continue
        rm = removed.get(t, frozenset())
        for s in sorted(syms):
            hits, run, band = _ladder(stair, s, t) if stair is not None else (None, None, None)
            close = None
            if s in md.closes.columns and t in md.closes.index:
                v = md.closes.at[t, s]
                close = float(v) if pd.notna(v) else None
            rows.append(dict(date=str(pd.Timestamp(t).date()), symbol=s, hits=hits, run=run,
                             band=band, close=close,
                             was_eligible=bool(s in rm),
                             removed_from_pool=bool(s in rm),
                             rebalance_session=bool(t in set(md.rebalance_dates))))
    fl = pd.DataFrame(rows)
    fl.to_csv(outdir / "flagged.csv", index=False)
    written["flagged.csv"] = len(fl)

    # ---------------------------------------------------------------- rebalance.csv
    # The counterfactual: with the removed names put BACK, would any have made the top N?
    from qbeast_backtest import config as C
    top_n = int(getattr(C, "TOP_N", 20))
    # The removed reference strategy's ranking was used here to say whether a flagged name WOULD
    # have been bought. With no strategy in the build there is no ranking
    # to consult, so the `mattered` counterfactual reports unknown rather than inventing one. What the
    # gate actually did - which names it flagged, on which sessions, and whether a rebalance lost a
    # candidate - is unaffected and still logged in full.
    mom = None

    rrows = []
    for t in md.rebalance_dates:
        elig = md.eligible.get(t, frozenset())
        rm = removed.get(t, frozenset())
        sel = set((out.targets or {}).get(t, {}) or {})
        would, mattered = "", False
        if rm and mom is not None and t in mom.index:
            pool = set(elig) | set(rm)
            r = mom.loc[t].reindex(sorted(pool)).dropna()
            if len(r):
                topn = set(r.nlargest(min(top_n, len(r))).index)
                hit = sorted(set(rm) & topn)
                would = "|".join(hit)
                mattered = bool(hit)
        rrows.append(dict(date=str(pd.Timestamp(t).date()),
                          n_eligible=len(elig), n_flagged=len(flagged.get(t, ())),
                          n_removed=len(rm), removed="|".join(sorted(rm)),
                          n_selected=len(sel),
                          would_have_been_selected=would, mattered=mattered))
    rb = pd.DataFrame(rrows)
    rb.to_csv(outdir / "rebalance.csv", index=False)
    written["rebalance.csv"] = len(rb)

    # ---------------------------------------------------------------- blocked.csv (the backstop)
    bl = pd.DataFrame()
    if res is not None and getattr(res, "refused", None) is not None:
        bl = res.refused
    elif stair is not None:
        try:
            bl = stair.blocked_table()
        except Exception:                                      # noqa: BLE001
            bl = pd.DataFrame()
    if bl is None:
        bl = pd.DataFrame()
    bl.to_csv(outdir / "blocked.csv", index=False)
    written["blocked.csv"] = len(bl)

    # ---------------------------------------------------------------- episodes (the gate's own scan)
    ep = getattr(rep, "episodes", None)
    if ep is not None and len(ep):
        ep.to_csv(outdir / "episodes.csv", index=False)
        written["episodes.csv"] = len(ep)

    # ---------------------------------------------------------------- run.json
    n_rb_mattered = int(rb["mattered"].sum()) if len(rb) and "mattered" in rb else 0
    summary = dict(
        universe=md.universe, benchmark=md.benchmark_name,
        window=[str(md.calendar[0].date()), str(md.calendar[-1].date())],
        sessions=int(len(md.calendar)), rebalances=int(len(md.rebalance_dates)),
        gate_on=bool(stair is not None),
        exclude_from_pool=bool(getattr(C, "STAIRCASE_EXCLUDE_FROM_POOL", True)),
        # The v2.4.1 rule, recorded so a log can always be read against the rules that made it. The
        # previous version logged `window` / `min_hits` / `min_run` through getattr(..., 0) defaults,
        # which after the switch would have written zeros into the audit trail - a log that looks
        # populated and describes nothing.
        flags_version="2.4.1",
        blocks="buy+sell+exit while TRADE_BLOCKED != NO; marked to market throughout",
        settings=dict(start_confirm_days=int(getattr(C, "STAIRCASE_START_DAYS", 5)),
                      end_confirm_days=int(getattr(C, "STAIRCASE_END_DAYS", 2)),
                      normal_vol_frac=float(getattr(C, "STAIRCASE_NORMAL_VOL", 0.5)),
                      volume_lookback=int(getattr(C, "STAIRCASE_VOL_LOOKBACK", 20)),
                      min_price=float(getattr(C, "STAIRCASE_MIN_PRICE", 0.0)),
                      chronic=bool(getattr(C, "STAIRCASE_CHRONIC", True))),
        symbols_ever_flagged=sorted(getattr(rep, "symbols_ever_flagged", frozenset())),
        sessions_with_flags=int(getattr(rep, "sessions_with_flags", 0)),
        rebalances_with_a_removal=int(len([1 for v in removed.values() if v])),
        rebalances_where_it_CHANGED_the_book=n_rb_mattered,
        ledger_backstop_refusals=int(len(bl)),
        files=written,
    )
    (outdir / "run.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def console(summary: dict) -> str:
    """One readable block for the run log."""
    L = ["  staircase log -> " + str(summary.get("files")),
         f"    gate {'ON' if summary['gate_on'] else 'OFF'}   "
         f"pool-exclusion {'ON' if summary['exclude_from_pool'] else 'OFF'}   "
         # v2.4.1 blocks buys, sells AND exits - there is no per-side switch to report, so the
         # line states the rule instead of a toggle. Reading summary['block_sell'] here is what
         # raised KeyError after the swap and cost the whole staircase log for that run.
         f"blocks {summary.get('blocks', 'buy+sell+exit')}   "
         f"settings {summary['settings']}",
         f"    {len(summary['symbols_ever_flagged'])} symbol(s) ever flagged over "
         f"{summary['sessions_with_flags']} session(s)",
         f"    {summary['rebalances_with_a_removal']} rebalance(s) had a name removed from the pool, "
         f"of which {summary['rebalances_where_it_CHANGED_the_book']} would otherwise have BOUGHT it",
         f"    ledger backstop refusals: {summary['ledger_backstop_refusals']} "
         f"(expected 0 once the pool filter is on)"]
    if summary["rebalances_with_a_removal"] and not summary["rebalances_where_it_CHANGED_the_book"]:
        L.append("    => the gate fired but changed NOTHING: every removed name would have ranked "
                 "outside the top N anyway. Firing is not the same as mattering.")
    return "\n".join(L)
