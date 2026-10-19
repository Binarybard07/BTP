r"""
qbeast_exec.report — shared performance stats + a self-contained HTML dashboard.

So that every strategy on the team reports the SAME metrics computed the SAME way. Give it a NAV series
(and optionally a benchmark, a TaxEngine and a SlippageModel) and it produces:

  * `performance_stats()` -> CAGR, vol, Sharpe, Sortino, max/avg drawdown, Calmar, underwater %, ...
  * `Dashboard`           -> a single self-contained .html file (no external assets, opens anywhere)

Plotly is optional: without it the dashboard still renders tables and KPI cards.

    from qbeast_exec.report import Dashboard, performance_stats
    Dashboard(nav, benchmark=bench, tax=tax_engine, slippage=slip, title="My Strategy").write("out.html")
"""
from __future__ import annotations

import html as _html
from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = ["performance_stats", "drawdown_table", "Dashboard"]

NAVY, GREEN, RED, GREY, BLUE, AMBER = "#10243a", "#12a150", "#c0392b", "#8a94a0", "#1f6fb0", "#e0a020"

_STAT_KEYS = ("start", "end", "years", "final", "total_return", "cagr", "ann_arith", "simple",
              "bars_per_yr", "vol", "sharpe", "sortino",
              "max_dd", "avg_dd", "avg_dd_episode", "n_dd_episodes", "pct_underwater", "calmar",
              "best_year", "worst_year", "hit_rate_monthly")


def performance_stats(nav: pd.Series, periods_per_year: int = 252, rf: float = 0.0) -> dict:
    """Standard performance metrics from a NAV series. An empty/1-point NAV returns NaNs, never raises.

    Drawdown is reported three ways, because they answer different questions:
      max_dd          the worst single peak-to-trough.
      avg_dd          TIME-WEIGHTED mean of the underwater curve (days at a new high count as 0)
                      -> "on average, how far below peak was the portfolio?"
      avg_dd_episode  mean TROUGH of each distinct underwater episode
                      -> "how deep does a typical drawdown get?"
    """
    nav = pd.Series(nav).dropna().sort_index()
    if len(nav) < 2:
        d = {k: float("nan") for k in _STAT_KEYS}
        d["n_dd_episodes"] = 0
        d["final"] = float(nav.iloc[-1]) if len(nav) else float("nan")
        return d
    r = nav.pct_change().dropna()

    # ELAPSED YEARS = CALENDAR TIME, not `len(nav) / periods_per_year`. A bar count over a nominal
    # constant is not the elapsed time, so it does not produce an annual rate. Measured on NSE: the
    # real rate is 247.6-248.0 sessions a year, so a flat 252 understated the years by 1.69% and
    # overstated every CAGR - by +0.17pp at a 10% figure and +0.71pp at a 36% one, since
    # reported = (1 + true)^1.0169 - 1.
    #
    # `periods_per_year` is still honoured for VOL and SHARPE below, where annualising a daily series
    # by a nominal 252 is the standard convention. It is no longer used to measure elapsed time.
    #
    # A DatetimeIndex is REQUIRED, and said so explicitly rather than quietly falling back to a bar
    # count. The first version of this change did add such a fallback - and a test immediately showed
    # it was unreachable: the monthly/yearly tables below call nav.resample(), so a non-dated index has
    # always failed a few lines later with pandas' own "Only valid with DatetimeIndex". A branch that
    # cannot run is not a safety net, it is a claim the code does not honour.
    if not isinstance(nav.index, pd.DatetimeIndex):
        raise TypeError("performance_stats() needs a DatetimeIndex: the annualisation is calendar "
                        "time and the yearly/monthly tables resample on dates.")
    if nav.index[-1] <= nav.index[0]:
        raise ValueError(f"performance_stats() got a NAV spanning no time "
                         f"({nav.index[0]} .. {nav.index[-1]}); cannot annualise.")
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    ppy = len(nav) / yrs
    total = nav.iloc[-1] / nav.iloc[0] - 1.0
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1.0 / yrs) - 1.0 if yrs > 0 else float("nan")
    # Uncompounded, two readings - see backtest.stats() for why both exist.
    #   ann_arith  mean daily return x the ACTUAL sessions/year  ("what an average day earned")
    #   simple     total return / elapsed years                  ("the whole run, spread evenly" -
    #                                                             the basis a 15% uncompounded hurdle
    #                                                             is stated on)
    ann_arith = float(r.mean() * ppy)
    simple = float(total / yrs) if yrs > 0 else float("nan")
    vol = r.std() * np.sqrt(periods_per_year)
    ex = r - rf / periods_per_year
    # DEGENERATE-SERIES GUARD. `r.std() > 0` is not a sufficient test. A NAV that is pure interest
    # accrual on idle cash (an all-cash strategy, or a paper book before its first fill) has a std of
    # ~4e-8: it passes `> 0`, and mean/std then explodes to ~1e13. That is not a large Sharpe ratio, it
    # is a division by nothing - the ratio is undefined when no risk was taken. A genuine return series
    # has a std many TIMES its own mean; a deterministic accrual has a std a few ten-thousandths of it.
    # Requiring dispersion of at least 1% of |mean| separates the two with a ~100x margin and leaves
    # every real series untouched.
    _rs, _rm = float(r.std()), float(abs(r.mean()))
    _riskless = (not np.isfinite(_rs)) or _rs <= 0.0 or _rs < _rm * 1e-2
    dn = r[r < 0]
    under = nav / nav.cummax() - 1.0
    is_under = under < 0
    ep = (is_under & ~is_under.shift(1, fill_value=False)).cumsum()[is_under]
    troughs = under[is_under].groupby(ep).min() if len(ep) else pd.Series(dtype=float)
    mdd = float(under.min())
    m = nav.resample("ME").last().pct_change().dropna()
    a = nav.resample("YE").last().pct_change().dropna()
    return dict(
        start=nav.index[0], end=nav.index[-1], years=yrs, final=float(nav.iloc[-1]),
        total_return=total * 100, cagr=cagr * 100, vol=float(vol * 100),
        ann_arith=ann_arith * 100, simple=simple * 100, bars_per_yr=ppy,
        sharpe=float("nan") if _riskless else float(ex.mean() / r.std() * np.sqrt(periods_per_year)),
        sortino=float("nan") if (_riskless or not len(dn) or dn.std() <= 0)
        else float(ex.mean() / dn.std() * np.sqrt(periods_per_year)),
        max_dd=mdd * 100, avg_dd=float(under.mean()) * 100,
        avg_dd_episode=float(troughs.mean()) * 100 if len(troughs) else 0.0,
        n_dd_episodes=int(len(troughs)), pct_underwater=float(is_under.mean() * 100),
        calmar=cagr / abs(mdd) if mdd < 0 else float("nan"),
        best_year=float(a.max() * 100) if len(a) else float("nan"),
        worst_year=float(a.min() * 100) if len(a) else float("nan"),
        hit_rate_monthly=float((m > 0).mean() * 100) if len(m) else float("nan"))


def drawdown_table(nav: pd.Series, top: int = 10) -> pd.DataFrame:
    """The `top` deepest drawdown episodes: peak date, trough date, recovery date, depth, length."""
    nav = pd.Series(nav).dropna().sort_index()
    if len(nav) < 2:
        return pd.DataFrame()
    under = nav / nav.cummax() - 1.0
    is_under = under < 0
    if not is_under.any():
        return pd.DataFrame()
    ep = (is_under & ~is_under.shift(1, fill_value=False)).cumsum()
    rows = []
    for _, idx in under[is_under].groupby(ep[is_under]).groups.items():
        seg = under.loc[idx]
        trough = seg.idxmin()
        start = seg.index[0]
        recovered = seg.index[-1] != under.index[-1]
        rows.append(dict(peak=start, trough=trough, end=seg.index[-1],
                         depth_pct=float(seg.min() * 100), days=len(seg),
                         days_to_trough=int(seg.index.get_loc(trough)) + 1,
                         recovered=recovered))
    return (pd.DataFrame(rows).sort_values("depth_pct").head(top).reset_index(drop=True))


# --------------------------------------------------------------------------- dashboard
def _fmt(v, dp=2, pct=False, money=False):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "&mdash;"
    if isinstance(v, (pd.Timestamp,)):
        return v.strftime("%d-%m-%Y")
    if money:
        return f"{v:,.0f}"
    return f"{v:,.{dp}f}{'%' if pct else ''}"


def _card(label, value, good=None, sub="") -> str:
    color = NAVY if good is None else (GREEN if good else RED)
    return (f'<div class="kpi"><div class="kv" style="color:{color}">{value}</div>'
            f'<div class="kl">{_html.escape(str(label))}</div>'
            f'{f"<div class=kd>{sub}</div>" if sub else ""}</div>')


def _table(df: pd.DataFrame, max_rows: int = 500, float_dp: int = 2) -> str:
    if df is None or len(df) == 0:
        return "<p class='note'>No data.</p>"
    d = df.head(max_rows).copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].map(lambda x: f"{x:,.{float_dp}f}" if np.isfinite(x) else "—")
        elif pd.api.types.is_datetime64_any_dtype(d[c]):
            d[c] = d[c].dt.strftime("%d-%m-%Y")
    head = "".join(f"<th>{_html.escape(str(c))}</th>" for c in d.columns)
    body = "".join("<tr>" + "".join(f"<td>{_html.escape(str(v))}</td>" for v in row) + "</tr>"
                   for row in d.itertuples(index=False))
    note = (f"<p class='note'>Showing first {max_rows} of {len(df):,} rows.</p>"
            if len(df) > max_rows else "")
    return f"<div class='tw'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>{note}"


_CSS = """
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,
Helvetica,Arial,sans-serif;color:#16202b;background:#f4f6f8}
header{background:#10243a;color:#fff;padding:22px 30px}
header h1{margin:0;font-size:21px;font-weight:650}header p{margin:5px 0 0;opacity:.75;font-size:13px}
.kpis{display:flex;flex-wrap:wrap;gap:12px;padding:18px 30px}
.kpi{background:#fff;border:1px solid #e3e8ee;border-radius:10px;padding:12px 16px;min-width:150px;flex:1}
.kv{font-size:21px;font-weight:680}.kl{font-size:11px;color:#61707f;text-transform:uppercase;
letter-spacing:.5px;margin-top:3px}.kd{font-size:11px;color:#8a94a0;margin-top:3px}
nav{display:flex;flex-wrap:wrap;gap:4px;padding:0 30px;border-bottom:1px solid #dde3ea;background:#fff}
nav button{background:none;border:0;padding:11px 15px;font-size:13px;cursor:pointer;color:#61707f;
border-bottom:2px solid transparent}
nav button.on{color:#10243a;font-weight:640;border-bottom-color:#1f6fb0}
main{padding:20px 30px 50px}section{display:none}section.on{display:block}
.tcard{background:#fff;border:1px solid #e3e8ee;border-radius:10px;padding:16px 18px;margin-bottom:16px}
.tcard h3{margin:0 0 10px;font-size:15px}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:6px 9px;text-align:right;border-bottom:1px solid #eef2f6;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
th{background:#f7f9fb;font-weight:640;color:#41505f;position:sticky;top:0}
.note{font-size:12px;color:#61707f;line-height:1.55}
"""

_JS = """
document.querySelectorAll('nav button').forEach(function(b){b.onclick=function(){
document.querySelectorAll('nav button').forEach(function(x){x.classList.remove('on')});
document.querySelectorAll('section').forEach(function(x){x.classList.remove('on')});
b.classList.add('on');document.getElementById(b.dataset.t).classList.add('on');}});
"""


@dataclass
class Dashboard:
    """A self-contained HTML report. Everything except `nav` is optional.

    nav        : pd.Series  daily NAV (the strategy equity curve), DatetimeIndex.
    benchmark  : pd.Series  optional benchmark level series; rescaled to nav's first value.
    tax        : TaxEngine  optional; adds the FY tax statement + holding-period tabs.
    slippage   : SlippageModel optional; adds the realised-slippage tab.
    charges    : ChargesCalculator optional; adds the itemised charge schedule.
    trades     : pd.DataFrame optional; the fill log.
    title/subtitle : header text.
    """

    nav: pd.Series
    benchmark: pd.Series | None = None
    tax: object | None = None
    slippage: object | None = None
    charges: object | None = None
    trades: pd.DataFrame | None = None
    title: str = "Strategy report"
    subtitle: str = ""
    periods_per_year: int = 252

    # ---- pieces
    def stats(self) -> dict:
        return performance_stats(self.nav, self.periods_per_year)

    def _kpis(self, s: dict) -> str:
        cards = [
            _card("CAGR", _fmt(s["cagr"], 2, pct=True), (s["cagr"] or 0) > 0),
            _card("Total return", _fmt(s["total_return"], 1, pct=True)),
            _card("Sharpe", _fmt(s["sharpe"], 2), (s["sharpe"] or 0) > 1),
            _card("Max drawdown", _fmt(s["max_dd"], 1, pct=True), False,
                  f"avg {_fmt(s['avg_dd'], 2, pct=True)} &middot; {s['n_dd_episodes']} episodes"),
            _card("Calmar", _fmt(s["calmar"], 2)),
            _card("Underwater", _fmt(s["pct_underwater"], 1, pct=True), None, "of all days"),
            _card("Final", _fmt(s["final"], money=True), None,
                  f"{_fmt(s['start'])} &rarr; {_fmt(s['end'])}"),
        ]
        if self.benchmark is not None and len(self.benchmark.dropna()) > 1:
            b = performance_stats(self.benchmark.reindex(self.nav.index).ffill(), self.periods_per_year)
            cards.append(_card("vs benchmark", _fmt((s["cagr"] or 0) - (b["cagr"] or 0), 2, pct=True),
                               (s["cagr"] or 0) >= (b["cagr"] or 0),
                               f"bench CAGR {_fmt(b['cagr'], 2, pct=True)}"))
        return "".join(cards)

    def _chart(self) -> str:
        try:
            import plotly.graph_objects as go
            from plotly.offline import get_plotlyjs
        except Exception:                                          # noqa: BLE001 — plotly optional
            return ("<p class='note'>Install <code>plotly</code> for interactive charts; "
                    "tables below are complete without it.</p>"), ""
        nav = self.nav.dropna()
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=nav.index, y=nav.values, name="Strategy",
                                 line=dict(color=BLUE, width=2)))
        if self.benchmark is not None:
            b = self.benchmark.reindex(nav.index).ffill()
            if len(b.dropna()) > 1 and float(b.dropna().iloc[0]) != 0:
                b = b * float(nav.iloc[0]) / float(b.dropna().iloc[0])
                fig.add_trace(go.Scatter(x=b.index, y=b.values, name="Benchmark",
                                         line=dict(color=GREY, width=1.5, dash="dot")))
        fig.update_layout(template="plotly_white", height=420,
                          margin=dict(l=60, r=25, t=30, b=40), hovermode="x unified",
                          legend=dict(orientation="h", y=1.02, yanchor="bottom"))
        fig.update_xaxes(hoverformat="%d-%m-%Y")
        under = (nav / nav.cummax() - 1.0) * 100
        f2 = go.Figure(go.Scatter(x=under.index, y=under.values, fill="tozeroy",
                                  line=dict(color=RED, width=1), name="Drawdown %"))
        f2.update_layout(template="plotly_white", height=250,
                         margin=dict(l=60, r=25, t=30, b=40), hovermode="x unified")
        html = (fig.to_html(full_html=False, include_plotlyjs=False, config={"displaylogo": False})
                + f2.to_html(full_html=False, include_plotlyjs=False, config={"displaylogo": False}))
        return html, f"<script>{get_plotlyjs()}</script>"

    def _stats_table(self, s: dict) -> str:
        rows = [("Period", f"{_fmt(s['start'])} to {_fmt(s['end'])}  ({_fmt(s['years'], 1)} years)"),
                ("Final value", _fmt(s["final"], money=True)),
                ("Total return", _fmt(s["total_return"], 2, pct=True)),
                ("CAGR", _fmt(s["cagr"], 2, pct=True)),
                ("Volatility (ann.)", _fmt(s["vol"], 2, pct=True)),
                ("Sharpe", _fmt(s["sharpe"], 3)), ("Sortino", _fmt(s["sortino"], 3)),
                ("Max drawdown", _fmt(s["max_dd"], 2, pct=True)),
                ("Average drawdown (time-weighted)", _fmt(s["avg_dd"], 2, pct=True)),
                ("Average drawdown per episode", _fmt(s["avg_dd_episode"], 2, pct=True)),
                ("Drawdown episodes", f"{s['n_dd_episodes']}"),
                ("Time underwater", _fmt(s["pct_underwater"], 1, pct=True)),
                ("Calmar", _fmt(s["calmar"], 3)),
                ("Best / worst year", f"{_fmt(s['best_year'],1,pct=True)} / {_fmt(s['worst_year'],1,pct=True)}"),
                ("Monthly hit rate", _fmt(s["hit_rate_monthly"], 1, pct=True))]
        body = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
        return f"<div class='tw'><table><tbody>{body}</tbody></table></div>"

    def _tax_tabs(self) -> list[tuple[str, str]]:
        if self.tax is None:
            return []
        out = []
        try:
            st = self.tax.assess_all()
            if len(st):
                cols = [c for c in ("fy_label", "date_assessed", "realised_stcg", "realised_ltcg",
                                    "ltcg_exemption_used", "loss_setoff", "stcg_taxable", "ltcg_taxable",
                                    "stcg_tax", "ltcg_tax", "tax") if c in st.columns]
                tot = st["tax"].sum()
                out.append(("Tax", f"<div class='tcard'><h3>Financial-year capital-gains statement</h3>"
                                   f"<p class='note'>Total tax across all years: "
                                   f"<b>{tot:,.0f}</b>. Rates are date-dependent by SALE date "
                                   f"(Finance Act 2024, 23-Jul-2024). Losses net within a year.</p>"
                                   f"{_table(st[cols])}</div>"))
            hp = self.tax.holding_period_summary()
            if len(hp):
                out.append(("Holding periods",
                            f"<div class='tcard'><h3>Holding period by symbol</h3>"
                            f"<p class='note'>Median days held drives the STCG/LTCG mix — under 365 days "
                            f"everything realises at the higher short-term rate.</p>"
                            f"{_table(hp.reset_index())}</div>"))
        except Exception as e:                                     # noqa: BLE001
            out.append(("Tax", f"<div class='tcard'><p class='note'>Tax report unavailable: "
                               f"{_html.escape(str(e))}</p></div>"))
        return out

    def _slip_tab(self) -> list[tuple[str, str]]:
        if self.slippage is None:
            return []
        try:
            s = self.slippage.summary()
            df = self.slippage.realised()
            if not s:
                return []
            cards = "".join([
                _card("Fills", f"{s['fills']:,}"),
                _card("Adverse", _fmt(s["adverse_pct"], 1, pct=True), None,
                      f"favourable {_fmt(100 - s['adverse_pct'], 1, pct=True)}"),
                _card("Mean drag", _fmt(s["mean_eps_pct"], 4, pct=True)),
                _card("p5 / p95", f"{_fmt(s['p5_pct'],3,pct=True)} / {_fmt(s['p95_pct'],3,pct=True)}"),
                _card("Infeasible", f"{s['infeasible']:,}", s["infeasible"] == 0,
                      "breached participation guard")])
            return [("Slippage", f"<div class='tcard'><h3>Realised slippage</h3>"
                                 f"<div class='kpis' style='padding:0 0 12px'>{cards}</div>"
                                 f"<p class='note'>eps &gt; 0 = adverse (buy paid more / sell received "
                                 f"less). Mean drag should track the model's mu.</p>"
                                 f"{_table(df.tail(300))}</div>")]
        except Exception as e:                                     # noqa: BLE001
            return [("Slippage", f"<div class='tcard'><p class='note'>{_html.escape(str(e))}</p></div>")]

    def _charges_tab(self) -> list[tuple[str, str]]:
        if self.charges is None:
            return []
        try:
            sch = self.charges.schedule()
            return [("Charges", f"<div class='tcard'><h3>Itemised charge schedule "
                                f"({self.charges.cfg.name})</h3>"
                                f"<p class='note'>Per side, on a representative trade. "
                                f"Effective rates: BUY {self.charges.effective_rate('BUY')*100:.4f}%, "
                                f"SELL {self.charges.effective_rate('SELL')*100:.4f}%.</p>"
                                f"{_table(sch.reset_index().rename(columns={'index': 'component'}), float_dp=2)}"
                                f"</div>")]
        except Exception as e:                                     # noqa: BLE001
            return [("Charges", f"<div class='tcard'><p class='note'>{_html.escape(str(e))}</p></div>")]

    # ---- output
    def to_html(self) -> str:
        s = self.stats()
        chart, plotly_js = self._chart()
        tabs = [("Performance",
                 f"<div class='tcard'><h3>Equity curve &amp; drawdown</h3>{chart}</div>"
                 f"<div class='tcard'><h3>Statistics</h3>{self._stats_table(s)}</div>"),
                ("Drawdowns",
                 f"<div class='tcard'><h3>Deepest drawdown episodes</h3>"
                 f"<p class='note'>Max {_fmt(s['max_dd'],2,pct=True)} &middot; time-weighted average "
                 f"{_fmt(s['avg_dd'],2,pct=True)} &middot; typical episode "
                 f"{_fmt(s['avg_dd_episode'],2,pct=True)} &middot; underwater "
                 f"{_fmt(s['pct_underwater'],1,pct=True)} of days.</p>"
                 f"{_table(drawdown_table(self.nav, 25))}</div>")]
        tabs += self._charges_tab() + self._slip_tab() + self._tax_tabs()
        if self.trades is not None and len(self.trades):
            tabs.append(("Trades", f"<div class='tcard'><h3>Fills ({len(self.trades):,})</h3>"
                                   f"{_table(self.trades.tail(500))}</div>"))
        btns = "".join(f'<button data-t="t{i}"{" class=on" if i == 0 else ""}>{_html.escape(n)}</button>'
                       for i, (n, _) in enumerate(tabs))
        secs = "".join(f'<section id="t{i}"{" class=on" if i == 0 else ""}>{h}</section>'
                       for i, (_, h) in enumerate(tabs))
        sub = self.subtitle or (f"{_fmt(s['start'])} to {_fmt(s['end'])} · "
                                f"{_fmt(s['years'],1)} years · generated by qbeast_exec")
        return (f"<!doctype html><html><head><meta charset='utf-8'>"
                f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{_html.escape(self.title)}</title><style>{_CSS}</style>{plotly_js}</head><body>"
                f"<header><h1>{_html.escape(self.title)}</h1><p>{sub}</p></header>"
                f"<div class='kpis'>{self._kpis(s)}</div><nav>{btns}</nav><main>{secs}</main>"
                f"<script>{_JS}</script></body></html>")

    def write(self, path) -> str:
        import pathlib
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_html(), encoding="utf-8")
        return str(p)
