r"""
qbeast_backtest/dashboard.py
============================
THE FUND DASHBOARD: one self-contained dashboard.html with the performance, risk, cost, regime,
holdings, trade and audit tabs.

PORTED from Backtest_Core/dashboard.py, copied rather than imported so the two folders run
independently. Two things differ, both deliberate:

  * `stats` comes from `qbeast_backtest.report.stats` - the shared library's `performance_stats` plus
    the short key aliases this plotting code was written against (maxdd, yrs, avgdd, n_dd, best_yr,
    worst_yr, hit, total). Same values, verified to 1e-9; the aliases live in one place instead of 40
    renames through the figures.

  * `_gate_artifacts()` takes the frames from the `MarketData` the run already loaded instead of
    re-reading them from disk, and races the trend gates through this package's own stage 2 and stage
    3. The original raced them through `research_volscaled.simulate`, a research script this package
    does not carry. Racing the real stages means the comparison tab shows the strategy under each
    gate, not a parallel implementation of it.

Called by `report.write()`, which also writes equity.csv, summary.json and the books. Not an entry
point - run `qbeast-backtest`.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs
from plotly.subplots import make_subplots

from qbeast_backtest.config import (CASH_YIELD, COST_PCT, INDEX_SYMBOL,
                                    INITIAL_CAPITAL, LTCG_RATE, LTCG_RATE_NEW, LTCG_RATE_OLD,
                                    OUT_DIR, STCG_RATE, STCG_RATE_NEW, STCG_RATE_OLD, TOP_N)

NAVY, GREEN, RED, GREY, BLUE, AMBER = "#10243a", "#12a150", "#c0392b", "#8a94a0", "#1f6fb0", "#e0a020"
ANN = np.sqrt(252.0)
REG_COL = {"cash": GREY, "full": GREEN, "sized": AMBER}

# --- benchmark + trend-gate helpers (universe-agnostic) -------------------------------------------
# EVERY benchmark label is derived from config.INDEX_SYMBOL, so swapping the universe (NIFTY 50/100/
# 200/500, MidCap 50/100/150/250, SmallCap 150/250, ...) relabels every chart/table automatically.
# Every trend gate is handled everywhere, and all gates are raced on the comparison tab.
# GATE_ORDER / GATE_COLOR went with them: the gate keys and their chart colours.


# THE RUNNING STRATEGY, set by build() from the StrategyOutput. The charts label themselves with
# whatever is actually running instead of a hardcoded product name - this build ships no strategy, and
# the one that gets added is the recipient's.
_STRAT: dict = {"name": "strategy", "params": {}}


def _strategy_name() -> str:
    return str(_STRAT.get("name") or "strategy")


def _strategy_title() -> str:
    return _strategy_name().replace("_", " ").title()


def _strategy_params_html() -> str:
    """The strategy's own `params`, as it reported them. The config tab used to describe the reference
    strategy's design in prose, which is both that strategy's intellectual property and simply wrong
    for any other one."""
    p = _STRAT.get("params") or {}
    if not p:
        return ("the strategy reported no parameters. Publish them in "
                "<code>StrategyOutput(params=...)</code> and they appear here.")
    return " &middot; ".join(f"{k} <b>{v}</b>" for k, v in p.items())


def bench_label() -> str:
    """Display name for the benchmark index, from config.INDEX_SYMBOL (e.g. NIFTY100 -> 'NIFTY 100',
    NIFTYMIDCAP150 -> 'NIFTY MidCap 150', NIFTYSMLCAP250 -> 'NIFTY SmallCap 250')."""
    s = str(INDEX_SYMBOL).upper().replace("_", " ").replace("-", " ")
    s = s.replace("MIDCAP", " MidCap ").replace("SMLCAP", " SmallCap ").replace("SMALLCAP", " SmallCap ")
    s = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", s)          # split letters from a following number
    return re.sub(r"\s+", " ", s).strip() or str(INDEX_SYMBOL)


# gate_display() and gate_label() were REMOVED WITH THE STRATEGY. They existed only to name and
# describe its trend gates, and they printed that design - gate name, fitted window, penalty term -
# into the rendered page. A dashboard built for someone else's strategy must not describe this one.
#
# A strategy labels itself through `StrategyOutput.name` and `.params` - see `_STRAT`.




def _gate_artifacts(index_close, md=None):
    """REMOVED WITH THE STRATEGY. Returns None, which every caller already handles.

    This computed the removed strategy's gate levels and raced its gates against each other - all of
    it that strategy's design, not the framework's. The two charts that used it (the index-candle
    overlay and the gate-comparison tab) now return empty and `build()` drops their tabs.

    A strategy that wants its own signal overlaid should publish it in `StrategyOutput.diagnostics`,
    which is the channel the dashboard reads for everything else.
    """
    return None



# ------------------------------------------------------------------ helpers
def _fig(fig: go.Figure, height: int = 430) -> str:
    fig.update_xaxes(hoverformat="%d-%m-%Y")
    fig.update_layout(template="plotly_white", height=height, margin=dict(l=60, r=25, t=45, b=40),
                      hovermode="x unified", legend=dict(orientation="h", y=1.02, yanchor="bottom"))
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={"displaylogo": False, "scrollZoom": True})


def _card(label: str, value: str, good: bool | None = None, sub: str = "") -> str:
    color = NAVY if good is None else (GREEN if good else RED)
    return (f'<div class="kpi"><div class="kv" style="color:{color}">{value}</div>'
            f'<div class="kl">{label}</div>{f"<div class=kd>{sub}</div>" if sub else ""}</div>')


def _regime_state(trend_up, vol_regime, index) -> pd.Series:
    tu = trend_up.reindex(index).ffill().fillna(True).astype(bool)
    vr = vol_regime.reindex(index).ffill().fillna("volatile")
    return pd.Series(np.where(~tu, "cash", np.where(vr == "calm", "full", "sized")), index=index)


def _blocks(state: pd.Series):
    """Yield (state_label, x0, x1) for each contiguous run — for the shaded regime bands."""
    grp = (state != state.shift()).cumsum()
    for _, g in state.groupby(grp):
        yield g.iloc[0], g.index[0], g.index[-1]


# ------------------------------------------------------------------- panels
def panel_overview(nav, nav_pre, bench, invested) -> str:
    invested = invested.reindex(nav.index).ffill().fillna(0.0)
    eq = go.Figure()
    eq.add_scatter(x=nav_pre.index, y=nav_pre / nav_pre.iloc[0], name=f"{_strategy_name()} (gross, no tax)",
                   line=dict(color=GREEN, width=1.2, dash="dot"))
    eq.add_scatter(x=nav.index, y=nav / nav.iloc[0], name=f"{_strategy_name()} (after tax)",
                   line=dict(color=NAVY, width=2))
    eq.add_scatter(x=bench.index, y=bench / bench.iloc[0], name=bench_label(),
                   line=dict(color=GREY, width=1.5, dash="dash"))
    eq.update_yaxes(type="log", title="Growth of Rs 1 (log)")
    dd = go.Figure()
    dd.add_scatter(x=nav.index, y=(nav / nav.cummax() - 1) * 100, name=f"{_strategy_name()} (strategy)",
                   fill="tozeroy", line=dict(color=NAVY, width=1))
    dd.add_scatter(x=bench.index, y=(bench / bench.cummax() - 1) * 100, name=f"{bench_label()} (actual market)",
                   line=dict(color=RED, width=1.2, dash="dash"))
    dd.update_yaxes(title="Drawdown (%)")
    idle = (1 - invested / nav).clip(0, 1) * 100
    idle_rs = (nav - invested).clip(lower=0)
    ic = go.Figure()
    ic.add_scatter(x=idle.index, y=idle, name="idle cash (% of equity)", fill="tozeroy",
                   line=dict(color=AMBER, width=1))
    ic.update_yaxes(title="idle cash (% of equity)", range=[0, 100])
    inote = (f'<div class="note"><b>Idle cash = cash / (cash + holdings MTM).</b> On average '
             f'<b>{idle.mean():.0f}%</b> of equity sits in cash (avg Rs {idle_rs.mean():,.0f}); currently '
             f'<b>{idle.iloc[-1]:.0f}%</b> (Rs {idle_rs.iloc[-1]:,.0f}). Idle cash earns the '
             f'{CASH_YIELD:.1%} net cash yield — it is the crash-protection buffer, not dead money.</div>')
    return (f'<div class="tcard"><h3>Cumulative return, net of costs &amp; Indian capital-gains tax</h3>'
            f'{_fig(eq, 440)}</div>'
            f'<div class="tcard"><h3>Drawdown — strategy vs the actual market ({bench_label()})</h3>'
            f'{_fig(dd, 320)}</div>'
            f'<div class="tcard"><h3>Idle cash — how much capital is sitting out of the market</h3>'
            f'{inote}{_fig(ic, 300)}</div>')


def panel_regime(index_close, state, deployment, gate_extra=None) -> str:
    """The benchmark shaded by the strategy's reported posture, over its deployment.

    `state` comes from `StrategyOutput.posture` - full / sized / cash - which is contract vocabulary,
    so this chart works for any strategy. What those postures MEAN is the strategy's own design, so
    the caption reports how long each one held and does not explain them: the original explained the
    removed strategy's own posture semantics and overlaid its signal levels.
    """
    idx = index_close.reindex(state.index).ffill()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        row_heights=[0.72, 0.28],
                        subplot_titles=(f"{bench_label()} shaded by posture "
                                        f"(green=full, amber=sized, grey=cash)",
                                        "Deployed % of capital"))
    fig.add_scatter(x=idx.index, y=idx, name=bench_label(), line=dict(color=NAVY, width=1),
                    row=1, col=1)
    for lab, x0, x1 in _blocks(state):
        fig.add_vrect(x0=x0, x1=x1, fillcolor=REG_COL[lab], opacity=0.13, line_width=0,
                      layer="below", row=1, col=1)
    fig.add_scatter(x=deployment.index, y=(deployment * 100).clip(0, 100), name="deployed %",
                    fill="tozeroy", line=dict(color=NAVY, width=1), row=2, col=1)
    fig.update_yaxes(type="log", title="index (log)", row=1, col=1)
    fig.update_yaxes(title="%", range=[0, 105], row=2, col=1)
    share = state.value_counts(normalize=True) * 100
    note = (f'<div class="note">Time in each posture, as <b>{_strategy_name()}</b> reported it: '
            f'<b>fully invested</b> {share.get("full", 0):.0f}% &middot; '
            f'<b>sized</b> {share.get("sized", 0):.0f}% &middot; '
            f'<b>cash</b> {share.get("cash", 0):.0f}%. The lower panel is the share of capital '
            f'actually deployed each day - read it beside the return, because a flat NAV while in '
            f'cash is not the same result as a flat NAV while invested.</div>')
    return f'<div class="tcard"><h3>Regime map &amp; deployment</h3>{note}{_fig(fig, 560)}</div>'


def panel_index_chart(idx, gate_extra=None, level=None) -> str:
    """Charting-site-style OHLC candles for the benchmark, from the project's own index CSV.

    The original overlaid three curves on these candles - a moving average with a hysteresis band, a
    causal p-spline, and the active trend gate's effective level - and explained each in the caption.
    Those belonged to the removed strategy. The CANDLES do not: they are the benchmark this run is
    measured against, and every strategy has one.

    `level` is an optional series a strategy can publish as `diagnostics["level"]` - whatever curve it
    gates, ranks or sizes on. Supply it and it is drawn over the candles; omit it and the chart is just
    the benchmark. That is the generic version of what the overlay used to be.
    """
    if idx is None or getattr(idx, "empty", True):
        return '<div class="tcard"><p>Index OHLC unavailable.</p></div>'
    fig = go.Figure()
    fig.add_candlestick(x=idx.index, open=idx["open"], high=idx["high"], low=idx["low"],
                        close=idx["close"], name=f"{bench_label()} (OHLC)",
                        increasing_line_color=GREEN, decreasing_line_color=RED,
                        line=dict(width=0.8))
    drawn = ""
    if level is not None and len(level):
        lv = level.reindex(idx.index)
        fig.add_scatter(x=lv.index, y=lv, name=f"{_strategy_name()} level",
                        line=dict(color=AMBER, width=2.0))
        drawn = (f' The amber line is the level <b>{_strategy_name()}</b> published as '
                 f'<code>diagnostics["level"]</code>.')
    fig.update_yaxes(title=f"{bench_label()} (price)")
    fig.update_layout(xaxis_rangeslider_visible=True)
    note = (f'<div class="note">The <b>{bench_label()}</b> OHLC '
            f'(<code>{INDEX_SYMBOL}.csv</code>) as green/red candles, drawn from this project\'s own '
            f'data rather than a vendor chart - so what you see here is exactly what the backtest '
            f'read.{drawn} A strategy can overlay its own curve by publishing it as '
            f'<code>diagnostics["level"]</code>.'
            f' <b>Note:</b> there is no volume underlay because the index feed carries none '
            f'(0 on every row - only individual stocks have volume). Drag the range-slider or '
            f'scroll to zoom.</div>')
    return (f'<div class="tcard"><h3>{bench_label()} candles</h3>{note}{_fig(fig, 580)}</div>')


def panel_gate_compare(*_a, **_k) -> str:
    """REMOVED WITH THE STRATEGY - it raced the removed strategy's gates by calendar year."""
    return ""


def panel_yearly(nav, bench, invested) -> str:
    invested = invested.reindex(nav.index).ffill().fillna(0.0)
    ry = nav.resample("YE").last().pct_change().dropna() * 100
    by = bench.resample("YE").last().pct_change().dropna() * 100
    yrs = sorted(set(nav.index.year))
    fig = go.Figure()
    fig.add_bar(x=[y for y in yrs if y in ry.index.year],
                y=[ry[ry.index.year == y].iloc[0] for y in yrs if y in ry.index.year],
                name=_strategy_name(), marker_color=NAVY)
    fig.add_bar(x=[y for y in yrs if y in by.index.year],
                y=[by[by.index.year == y].iloc[0] for y in yrs if y in by.index.year],
                name=bench_label(), marker_color=GREY)
    fig.update_layout(barmode="group")
    fig.update_yaxes(title="Return (%)")
    fig.update_xaxes(hoverformat="")
    r = nav.pct_change()
    rows = []
    for y in yrs:
        ny, byv, iy = nav[nav.index.year == y], bench[bench.index.year == y], invested[invested.index.year == y]
        if len(ny) < 2:
            continue
        geo = (ny.iloc[-1] / ny.iloc[0] - 1) * 100
        ari = r[r.index.year == y].mean() * 252 * 100
        bgeo = (byv.iloc[-1] / byv.iloc[0] - 1) * 100
        sdd = (ny / ny.cummax() - 1).min() * 100
        idd = (byv / byv.cummax() - 1).min() * 100
        idl = float((1 - iy / ny).clip(0, 1).mean() * 100)
        idlr = float((ny - iy).clip(lower=0).mean())
        red = " style='color:#c0392b'" if sdd < idd else ""
        rows.append(f"<tr><td>{y}</td><td class=num>{geo:+.1f}</td><td class=num>{ari:+.1f}</td>"
                    f"<td class=num>{bgeo:+.1f}</td><td class=num{red}>{sdd:.1f}</td>"
                    f"<td class=num>{idd:.1f}</td><td class=num>{idl:.0f}%</td>"
                    f"<td class=num>Rs {idlr:,.0f}</td></tr>")
    note = ('<div class="note">Per year — strategy return as <b>geometric</b> (compounded) and '
            '<b>arithmetic</b> (mean daily &times; 252, R&rsquo;s Return.annualized geometric=FALSE); '
            'index return; worst <b>intra-year drawdown</b>, strategy vs the actual market (red = the '
            'strategy drew down deeper than the index that year); and average idle cash.</div>')
    tbl = ("<table class=tbl><thead><tr><th>Year</th><th>Strat geom %</th><th>Strat arith %</th>"
           "<th>Index %</th><th>Strat maxDD %</th><th>Index maxDD %</th><th>Idle %</th>"
           "<th>Idle capital</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
    return (f'<div class="tcard"><h3>Calendar-year returns</h3>{_fig(fig, 400)}</div>'
            f'<div class="tcard"><h3>Year-by-year detail</h3>{note}{tbl}</div>')


def panel_monthly(nav) -> str:
    m = nav.resample("ME").last().pct_change().dropna() * 100
    df = pd.DataFrame({"y": m.index.year, "mo": m.index.month, "r": m.values})
    grid = df.pivot_table(index="y", columns="mo", values="r").reindex(columns=range(1, 13))
    vmax = float(np.nanmax(np.abs(grid.values)))
    fig = go.Figure(go.Heatmap(
        z=grid.values, x=list("JFMAMJJASOND"), y=[str(y) for y in grid.index],
        colorscale="RdYlGn", zmid=0, zmin=-vmax, zmax=vmax,
        text=np.where(np.isfinite(grid.values), np.round(grid.values, 0), None),
        texttemplate="%{text}", textfont=dict(size=9), colorbar=dict(title="%")))
    fig.update_yaxes(autorange="reversed")
    return f'<div class="tcard"><h3>Monthly returns (%)</h3>{_fig(fig, max(320, 26 * len(grid) + 90))}</div>'


def panel_trades(led) -> str:
    if not led.trades:
        return '<div class="tcard"><p>No trades.</p></div>'
    fr = pd.DataFrame(led.trades)
    fr["side"] = np.where(fr["qty"] > 0, "BUY", "SELL")
    rows = "".join(
        f'<tr><td>{r.date.date()}</td><td>{r.symbol}</td>'
        f'<td><span class="pill {"buy" if r.qty>0 else "sell"}">{r.side}</span></td>'
        f'<td class="num">{abs(int(r.qty)):,}</td><td class="num">{r.px:,.1f}</td>'
        f'<td class="num">{r.cost:,.0f}</td></tr>'
        for r in fr.tail(60).itertuples())
    return (f'<div class="tcard"><h3>Executed trades (last 60 of {len(fr):,})</h3>'
            f'<div class="note">Fills at next-day open; commission + per-stock slippage shown as cost. '
            f'Total costs Rs {led.cost_paid:,.0f}, tax Rs {led.tax_paid:,.0f}.</div>'
            f'<table class="tbl"><thead><tr><th>Date</th><th>Symbol</th><th>Side</th><th>Qty</th>'
            f'<th>Price</th><th>Cost</th></tr></thead><tbody>{rows}</tbody></table></div>')


def panel_config(led, s) -> str:
    recon = ""
    _nn = OUT_DIR / "nautilus_nav.csv"
    if _nn.exists():
        try:
            nn = pd.read_csv(_nn, index_col=0, parse_dates=True).iloc[:, 0].dropna()
            # calendar years, to match backtest.stats() - a bar count over 252 is not elapsed time
            yrs = (nn.index[-1] - nn.index[0]).days / 365.25
            ncagr = ((nn.iloc[-1] / nn.iloc[0]) ** (1 / yrs) - 1) * 100
            ndd = float((nn / nn.cummax() - 1).min()) * 100
            recon = (f" <b>Reconciled</b>: {ncagr:.2f}% CAGR / {ndd:.1f}% maxDD on Nautilus vs "
                     f"{s['cagr']:.2f}% / {s['maxdd']:.1f}% on the reference ledger.")
        except Exception:
            recon = ""
    note = ('<div class="note">Gross is before Indian capital-gains tax, net is after it. The gap is what the book does not keep: short-term gains are taxed at the higher rate, so a strategy that turns over faster hands more of its return to tax for the same gross result.</div>')
    rows = [("Selection", f"top {TOP_N}, ranked by {_strategy_name()}, on the point-in-time universe"),
            ("Execution engine", f"<b>NautilusTrader v1.228.0</b> &mdash; orders / fills / positions, "
                                 f"per-stock slippage &amp; commission; Indian FIFO tax + idle-cash yield as a "
                                 f"faithful overlay (Nautilus models neither). The custom ledger is retained "
                                 f"only as the reconciliation reference.{recon}"),
            # The strategy describes ITSELF here, from StrategyOutput.params. These rows used to
            # spell out the reference strategy's design, which is that strategy's own work and is
            # simply false for any other one.
            ("Strategy", f"<b>{_strategy_name()}</b>"),
            ("Strategy parameters", _strategy_params_html()),
            ("Costs / tax", f"{COST_PCT:.2%}/side + per-stock slippage; FIFO STCG {STCG_RATE_OLD:.0%}&rarr;"
                            f"{STCG_RATE_NEW:.0%} / LTCG {LTCG_RATE_OLD:.0%}&rarr;{LTCG_RATE_NEW:.1%} "
                            f"(23-Jul-2024, by sale date); idle cash {CASH_YIELD:.1%} net"),
            ("Frictions paid", f"costs Rs {led.cost_paid:,.0f} &middot; tax Rs {led.tax_paid:,.0f} &middot; "
                               f"interest Rs {led.interest:,.0f} &middot; {len(led.trades):,} trades"),
            ("Data", "existing clean source, read-only (nothing copied or modified)")]
    tbl = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
    return f'<div class="tcard"><h3>Configuration</h3>{note}<table class="tbl">{tbl}</table></div>'


def panel_returns(s, s_pre, sb, led) -> str:
    """Gross (no tax) vs after-tax returns: CAGR (geometric) and annualised arithmetic (geometric=FALSE)."""
    def r(metric, pre, post, drag, idx):
        return (f"<tr><td>{metric}</td><td class='num'>{pre}</td><td class='num'>{post}</td>"
                f"<td class='num'>{drag}</td><td class='num'>{idx}</td></tr>")
    rows = [
        r("CAGR (geometric)", f"{s_pre['cagr']:.2f}%", f"{s['cagr']:.2f}%",
          f"{s_pre['cagr']-s['cagr']:.2f} pp", f"{sb['cagr']:.2f}%"),
        r("Annualised arithmetic (geom=FALSE)", f"{s_pre['ann_arith']:.2f}%", f"{s['ann_arith']:.2f}%",
          f"{s_pre['ann_arith']-s['ann_arith']:.2f} pp", f"{sb['ann_arith']:.2f}%"),
        r("Simple / uncompounded (total &divide; years)", f"{s_pre['simple']:.2f}%",
          f"{s['simple']:.2f}%", f"{s_pre['simple']-s['simple']:.2f} pp", f"{sb['simple']:.2f}%"),
        r("Total return", f"{s_pre['total']:.0f}%", f"{s['total']:.0f}%", "&mdash;", f"{sb['total']:.0f}%"),
        r("Final capital (Rs 1M start)", f"Rs {s_pre['final']:,.0f}", f"Rs {s['final']:,.0f}",
          f"Rs {s_pre['final']-s['final']:,.0f}", f"Rs {sb['final']:,.0f}"),
    ]
    note = ('<div class="note"><b>CAGR</b> is compounded over CALENDAR years (not bars&divide;252: NSE '
            f'trades ~{s.get("bars_per_yr", 247.8):.1f} sessions a year, and a 252-session &ldquo;year&rdquo; '
            f'overstated every CAGR). <b>Annualised arithmetic (geometric=FALSE)</b> = mean daily return '
            f'&times; {s.get("bars_per_yr", 247.8):.1f} (R&rsquo;s Return.annualized, geometric=FALSE); it '
            f'sits above the compounded CAGR at a modest return - though it is multiplied by the year rather than compounded over it, so a high-return series can see CAGR overtake it. <b>Simple</b> = total return &divide; elapsed years, with no '
            f'reinvestment credited &mdash; the basis an uncompounded hurdle is stated on. '
            f'<b>Tax drag</b> = the cost of Indian FIFO capital-gains tax (STCG {STCG_RATE_OLD:.0%}&rarr;'
            f'{STCG_RATE_NEW:.0%} / LTCG {LTCG_RATE_OLD:.0%}&rarr;{LTCG_RATE_NEW:.1%}, by sale date from '
            f'23-Jul-2024); total tax paid Rs {led.tax_paid:,.0f} over the run.</div>')
    return (f'<div class="tcard"><h3>Returns &mdash; gross vs after-tax</h3>{note}'
            f'<table class="tbl"><thead><tr><th>Metric</th><th>Gross (no tax)</th><th>After tax</th>'
            f'<th>Tax drag</th><th>{bench_label()}</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def panel_rebalance(log) -> str:
    if not log:
        return '<div class="tcard"><p>No rebalances recorded.</p></div>'
    note = ('<div class="note"><b>Rebalance cadence:</b> monthly, on the <b>first trading day of each '
            'month</b> — the same schedule as the research pipeline. Orders from that day&rsquo;s signals '
            'fill at the <b>next day&rsquo;s open</b>. Between rebalances the book is held; only the '
            'monthly momentum re-rank and the regime posture (how much to deploy) change what is held. '
            f'Total rebalances: <b>{len(log)}</b>.</div>')
    rows = []
    for e in log[::-1][:120]:
        col = REG_COL.get(e["posture"], NAVY)
        rows.append(f"<tr><td>{e['date'].date()}</td>"
                    f"<td><span class=pill style='background:{col}'>{e['posture'].upper()}</span></td>"
                    f"<td class=num>{e['deploy']*100:.0f}%</td><td class=num>{e['n']}</td>"
                    f"<td class=num>+{e['added']}</td><td class=num>&minus;{e['dropped']}</td></tr>")
    tbl = ("<table class=tbl><thead><tr><th>Rebalance date</th><th>Posture</th><th>Deployed</th>"
           "<th>#Held</th><th>Added</th><th>Dropped</th></tr></thead><tbody>"
           + "".join(rows) + "</tbody></table>")
    return f'<div class="tcard"><h3>Monthly rebalance reports (latest 120)</h3>{note}{tbl}</div>'


def panel_signals(mom, memb, closes, log) -> str:
    if mom is None or closes is None:
        return '<div class="tcard"><p>No signal data.</p></div>'
    asof = closes.dropna(how="all").index[-1]
    elig = memb.eligible(asof) if memb is not None else set(closes.columns)
    price = closes.reindex([asof]).ffill().iloc[-1]
    row = mom.reindex([asof]).ffill().iloc[-1].dropna()
    ranked = row[[s for s in row.index if s in elig and np.isfinite(price.get(s, np.nan))]].sort_values(
        ascending=False)
    prev = set(log[-2]["names"]) if len(log) >= 2 else set()
    rows = []
    for i, (s, mv) in enumerate(ranked.items()):
        if i < TOP_N:
            lab, col = ("BUY", BLUE) if s not in prev else ("HOLD", GREEN)
        else:
            lab, col = ("SELL", RED) if s in prev else ("flat", GREY)
        rows.append(f"<tr><td class=num>{i+1}</td><td>{s}</td>"
                    f"<td><span class=pill style='background:{col}'>{lab}</span></td>"
                    f"<td class=num>{mv*100:+.1f}%</td><td class=num>{price.get(s, float('nan')):,.1f}</td></tr>")
    note = (f'<div class="note">Signal grid as of <b>{asof.date()}</b>: every point-in-time-eligible name '
            f'ranked by the strategy&rsquo;s own signal. The top {TOP_N} are <b>held</b> — <b>HOLD</b>, or <b>BUY</b> if '
            f'newly entering since last month; names that fell out of the top {TOP_N} but were held are '
            f'<b>SELL</b>; the rest are flat. This is the <i>selection</i> layer; the regime posture then '
            f'scales how much of the book is actually deployed.</div>')
    tbl = ("<table class=tbl><thead><tr><th>Rank</th><th>Symbol</th><th>Signal</th>"
           "<th>signal</th><th>Price</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
    return f'<div class="tcard"><h3>Signal grid — buy / hold / sell, symbol by symbol</h3>{note}{tbl}</div>'


def panel_prediction(pj) -> str:
    if not pj:
        return ('<div class="tcard"><p>Run <code>python research_predict.py</code> to generate the '
                'prediction results (writes reports/prediction.json).</p></div>')
    fig = go.Figure()
    fig.add_scatter(x=pj["dates"], y=pj["prob"], name="model P(crash) — monthly avg",
                    line=dict(color=RED, width=1.5))
    fig.add_scatter(x=pj["dates"], y=pj["crash"], name="realised >5% drop ahead — monthly avg",
                    line=dict(color=NAVY, width=1, dash="dot"))
    fig.update_yaxes(title="probability / frequency")
    cards = (_card("Crash AUC (OOS)", f'{pj["crash_auc"]:.3f}', pj["crash_skill"],
                   "0.50 = coin flip; &gt;0.60 usable")
             + _card("Direction IC (OOS)", f'{pj["dir_ic"]:+.3f}', pj["dir_skill"],
                     f't = {pj["dir_t"]:+.2f}, independent windows')
             + _card("ML hit-rate", f'{pj["dir_hit"]:.0f}%', pj["dir_hit"] > pj["dir_hit_ma"],
                     f'100-DMA rule {pj["dir_hit_ma"]:.0f}%'))
    note = ('<div class="warn2"><b>Verdict — prediction does not beat the reactive rules.</b> A '
            f'walk-forward LightGBM crash classifier scores OOS AUC <b>{pj["crash_auc"]:.3f}</b> '
            f'(no better than the 0.50 coin-flip); index-direction IC is <b>{pj["dir_ic"]:+.3f}</b> '
            f'(t={pj["dir_t"]:+.2f}), and its hit-rate ({pj["dir_hit"]:.0f}%) does not beat the simple '
            f'100-DMA trend rule ({pj["dir_hit_ma"]:.0f}%). This is exactly why the framework '
            '<b>adapts</b> to the regime rather than trying to <b>predict</b> it — the chart shows the '
            'model&rsquo;s crash probability never lines up with the actual drops.</div>')
    return (f'<div class="tcard"><h3>Trend / crash prediction — tested and rejected</h3>{note}'
            f'<div class="kpis" style="padding:2px 0 12px">{cards}</div>{_fig(fig, 380)}</div>')


def panel_features(pj) -> str:
    if not pj:
        return ('<div class="tcard"><p>Run <code>python research_predict.py</code> first '
                '(writes reports/prediction.json).</p></div>')
    uic, imp = pj["uic"], pj["importance"]
    order = sorted(pj["feats"], key=lambda f: abs(uic.get(f, 0.0)), reverse=True)
    fig = go.Figure(go.Bar(x=[abs(uic[f]) for f in order], y=order, orientation="h", marker_color=BLUE))
    fig.update_xaxes(title="|univariate IC| with forward 21-day index return")
    fig.update_yaxes(autorange="reversed")
    best = order[0]
    rows = "".join(f"<tr><td>{f}</td><td class=num>{uic[f]:+.3f}</td><td class=num>{imp[f]:.0f}</td></tr>"
                   for f in order)
    tbl = ("<table class=tbl><thead><tr><th>Feature</th><th>Univariate IC</th>"
           "<th>LightGBM gain</th></tr></thead><tbody>" + rows + "</tbody></table>")
    note = ('<div class="warn2"><b>Feature selection — nothing carries a tradable edge.</b> Across these '
            '16 index momentum / volatility / trend / breadth features, the strongest univariate '
            f'information coefficient with the forward return is only <b>{uic[best]:+.3f}</b> '
            f'(<code>{best}</code>) — negligible. This matches the project&rsquo;s Boruta result (all '
            'return-features rejected): the gradient-boosted model has essentially no signal to exploit, '
            'so it cannot beat the simple regime rules. LightGBM &ldquo;gain&rdquo; still ranks the '
            'features it splits on, but importance without out-of-sample IC is just in-sample noise.</div>')
    return (f'<div class="tcard"><h3>Feature selection — univariate IC &amp; model importance</h3>{note}'
            f'{_fig(fig, 430)}{tbl}</div>')


def panel_logs() -> str:
    """A directory of every generated log / report, with a one-line description and a direct link."""
    items = [
        ("daily_log.txt", "Day-by-day narrative: start-of-day book, every order (commission+slippage split "
                          "and realized STCG/LTCG), FY tax-assessment days, end-of-day cash / MTM / NAV."),
        ("daily_portfolio.txt", "Day-by-day portfolio STATUS table (posture, deployed %, holdings, cash, MTM, "
                                "NAV, drawdown, charges, tax) — one row per trading day."),
        ("daily_portfolio.csv", "The same day-by-day status as CSV, for analysis / plotting."),
        ("daily_holdings.csv", "Every (day, stock) position held: quantity, price, value, weight."),
        ("FY_TAX_STATEMENT.csv", "FINANCIAL-YEAR capital-gains tax statement: realised STCG/LTCG, exemption, STCG/LTCG tax (date-dependent rates), tax regime, total tax, date assessed (31 Mar)."),
        ("HOLDING_PERIOD.csv", "PER-SYMBOL holding periods: lots, avg/max days held, % long-term, realised STCG/LTCG gain (from the FIFO lot ledger). Lot-level detail in HOLDING_PERIOD_LOTS.csv."),
        ("FY_EQUITY.csv", "FY-BOUNDARY equity: NAV = cash + MTM at each 31-Mar (FY end) and 1-Apr (next FY start), year-wise, with the FY tax deducted at the boundary."),
        ("executed_trades.csv", "Every executed fill: side, qty, price, trade value, cost, running position."),
        ("positions_report.csv", "Current holdings vs the expected target book &mdash; the buy / sell / hold order sheet."),
        ("equity.csv", "The daily equity (NAV) curve."),
        ("TARGET_BOOK.txt", "Today&rsquo;s target book from live.py (posture + names to hold, with rupees / shares)."),
        ("target_book.csv", "Today&rsquo;s target book as CSV."),
        ("symbol_plots/index.html", "Interactive per-symbol charts (147 names) with BUY / HOLD / SELL signals."),
        ("orderbook_backtest.csv", "NautilusTrader ORDER BOOK (backtest) &mdash; every order with status, filled qty, avg price, commission."),
        ("tradebook_backtest.csv", "NautilusTrader TRADE BOOK (backtest) &mdash; every fill with price / qty / value / commission."),
        ("book_by_stock_backtest.csv", "Backtest books rolled up STOCK-WISE (orders, trades, buy/sell qty &amp; value, realized PnL)."),
        ("orderbook_by_symbol_backtest.csv", "SYMBOL-WISE order book (backtest) &mdash; every order grouped by symbol, date-wise."),
        ("tradebook_by_symbol_backtest.csv", "SYMBOL-WISE trade book (backtest) &mdash; every fill grouped by symbol, date-wise, with running position."),
        ("orderbook_paper.csv", "NautilusTrader ORDER BOOK (paper/live) &mdash; orders with status, from the data_live feed."),
        ("tradebook_paper.csv", "NautilusTrader TRADE BOOK (paper/live) &mdash; fills."),
        ("book_by_stock_paper.csv", "Paper/live books rolled up STOCK-WISE."),
        ("nautilus_flow.png", "DIAGRAM &mdash; how an order flows through NautilusTrader (each stage&rsquo;s Nautilus classes labelled)."),
        ("NautilusTrader_Integration.docx", "DOC &mdash; how NautilusTrader is used in the system: role, full module map, order flow, order/trade books, and what it does vs doesn&rsquo;t model."),
    ]
    rows = []
    for fn, desc in items:
        ok = (OUT_DIR / fn).exists()
        link = (f'<a href="{fn}" target="_blank">{fn}</a>' if ok
                else f'{fn} <span style="color:#c0392b">(not generated yet)</span>')
        rows.append(f"<tr><td>{link}</td><td>{desc}</td></tr>")
    note = ('<div class="note">Every generated file lives in the <code>reports/</code> folder next to this '
            'dashboard, so the links resolve when you open <code>dashboard.html</code> from there. Regenerate '
            'the whole set &mdash; logs, figures and this dashboard &mdash; with <code>python rebuild_all.py</code>.</div>')
    tbl = ("<table class=tbl><thead><tr><th>File</th><th>What it contains</th></tr></thead><tbody>"
           + "".join(rows) + "</tbody></table>")
    return f'<div class="tcard"><h3>Logs &amp; downloadable reports</h3>{note}{tbl}</div>'


# --------------------------------------------------------------------- page
def _tab_slug(name: str) -> str:
    """A DOM-safe id for a tab. The tab NAME is human text and may contain an apostrophe ("Today's
    orders"), an ampersand ("Paper P&L") or spaces — dropping those straight into
    `onclick="show('...')"` terminated the JS string literal and silently killed that tab (the panel was
    rendered but unreachable). Ids/handlers use the slug; the button keeps the readable label."""
    return re.sub(r"[^A-Za-z0-9]+", "-", str(name)).strip("-").lower() or "tab"


def _write_page(out, title, subtitle, kpis, gate_note, tabs) -> None:
    btns = "".join(f'<button class="tab{" active" if i == 0 else ""}" '
                   f'onclick="show(\'{_tab_slug(n)}\')" id="t-{_tab_slug(n)}">{n}</button>'
                   for i, (n, _) in enumerate(tabs))
    bodies = "".join(f'<div class="body{" active" if i == 0 else ""}" id="b-{_tab_slug(n)}">{h}</div>'
                     for i, (n, h) in enumerate(tabs))
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title><script>{get_plotlyjs()}</script><style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f4f6f9;color:{NAVY}}}
header{{background:{NAVY};color:#fff;padding:16px 30px}} header h1{{margin:0;font-size:19px}}
header div{{opacity:.75;font-size:12px;margin-top:3px}}
.kpis{{display:flex;gap:12px;flex-wrap:wrap;padding:16px 30px}}
.kpi{{background:#fff;border:1px solid #e3e8ee;border-radius:10px;padding:12px 16px;min-width:150px}}
.kv{{font-size:21px;font-weight:700}} .kl{{font-size:11.5px;color:#5a6673;margin-top:2px}}
.kd{{font-size:10.5px;color:#8a94a0}}
.tabs{{display:flex;gap:6px;padding:0 30px;background:#eef1f6;flex-wrap:wrap}}
.tab{{border:0;background:#dfe5ec;padding:9px 16px;border-radius:8px 8px 0 0;cursor:pointer;font-size:13px}}
.tab.active{{background:#fff;font-weight:600}}
.body{{display:none;padding:18px 30px}} .body.active{{display:block}}
.chart,.tcard{{background:#fff;border:1px solid #e3e8ee;border-radius:10px;padding:10px 14px;margin-bottom:16px}}
.note{{font-size:12px;color:#5a6673;margin:2px 0 10px}}
.tcard h3{{margin:4px 0 8px;font-size:14px}}
.tbl{{width:100%;border-collapse:collapse;font-size:12px}}
.tbl th{{background:#10243a;color:#fff;text-align:left;padding:6px 8px;position:sticky;top:0}}
.tbl td{{border-bottom:1px solid #eef1f5;padding:5px 8px}}
.tbl .num{{text-align:right;font-variant-numeric:tabular-nums}}
.pill{{padding:1px 7px;border-radius:9px;font-size:11px;color:#fff}}
.pill.buy{{background:#12a150}} .pill.sell{{background:#c0392b}}
.warn2{{background:#fff8e1;border:1px solid #ffe0a3;color:#6b4e00;padding:10px 13px;border-radius:8px;font-size:12.5px;margin-bottom:10px}}
.warn{{background:#fdecea;border:1px solid #f5c6cb;color:#7b241c;padding:11px 15px;border-radius:8px;margin:0 30px 8px;font-size:13px}}
</style></head><body>
<header><h1>{title}</h1><div>{subtitle}</div></header>
<div class="kpis">{''.join(kpis)}</div>
{gate_note}
<div class="tabs">{btns}</div>{bodies}
<script>
function show(n){{
 document.querySelectorAll('.body').forEach(e=>e.classList.remove('active'));
 document.querySelectorAll('.tab').forEach(e=>e.classList.remove('active'));
 document.getElementById('b-'+n).classList.add('active');
 document.getElementById('t-'+n).classList.add('active');
 window.dispatchEvent(new Event('resize'));
}}
</script></body></html>"""
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}  ({out.stat().st_size/1024:.0f} KB)")


def build(res) -> None:
    # Label every chart with the strategy that actually ran. See _STRAT.
    _so = res.get("strategy")
    _STRAT["name"] = str(getattr(_so, "name", "") or "strategy")
    _STRAT["params"] = dict(getattr(_so, "params", None) or {})
    from qbeast_backtest.report import stats
    nav, bench = res["nav"], res["bench"]
    s, sb = res["stats"], stats(bench)
    s_pre, nav_pre = res["stats_pretax"], res["nav_pretax"]
    led = res["ledger"]
    state = _regime_state(res["trend_up"], res["vol_regime"], nav.index)
    deployment = (res["invested"] / nav)
    passed = s["maxdd"] >= sb["maxdd"]                       # less negative = shallower = good
    inv = res["invested"].reindex(nav.index).ffill().fillna(0.0)
    idle_pct = float((1 - inv / nav).clip(0, 1).mean() * 100)
    idle_rs = float((nav - inv).clip(lower=0).mean())
    import json
    _pjf = OUT_DIR / "prediction.json"
    pj = json.loads(_pjf.read_text(encoding="utf-8")) if _pjf.exists() else None
    try:
        from qbeast_backtest.data_io import load_index, trading_window
        idx_ohlc = trading_window(load_index())
    except Exception:
        idx_ohlc = None
    # `md` lets the race run on the panel already in memory instead of re-reading every frame.
    gate_extra = _gate_artifacts(res["index_close"], md=res.get("market"))

    # DISPLAY WINDOW: signals are COMPUTED on the full price history (trailing signals need their
    # warmup, and the data file can run past BACKTEST_END), but the dashboard must SHOW only the configured
    # backtest window. Without this the Regime-map / Trend-gate charts run on to the last date in the data
    # file, so the dashboard appeared to cover dates beyond config.BACKTEST_END.
    _lo, _hi = nav.index[0], nav.index[-1]

    def _clip(x):
        if isinstance(x, (pd.Series, pd.DataFrame)) and len(x):
            return x.loc[(x.index >= _lo) & (x.index <= _hi)]
        return x

    idxc_disp = _clip(res["index_close"])
    if idx_ohlc is not None:
        idx_ohlc = _clip(idx_ohlc)
    if gate_extra:
        gate_extra = {k: ({g: _clip(v) for g, v in val.items()} if isinstance(val, dict) else _clip(val))
                      for k, val in gate_extra.items()}

    # The title and subtitle come from the RUNNING strategy. They used to be hardcoded descriptions
    # of the reference strategy's two variants, chosen by BLEND_ALPHA - which named it, described its
    # design, and would be wrong for anything else.
    strat_name = _strategy_title()
    strat_sub = (" &middot; ".join(f"{k} {v}" for k, v in (_STRAT.get("params") or {}).items())
                 or "see the Configuration tab")
    posture_txt = strat_sub

    # FRICTIONS: the ledger tracks cost_paid as commission+slippage COMBINED, but each trade row keeps
    # them apart — so split them rather than reporting one blended number. These are realised rupees that
    # actually left the cash balance, not estimates.
    _tr = pd.DataFrame(led.trades) if getattr(led, "trades", None) else pd.DataFrame()
    tax_paid = float(getattr(led, "tax_paid", 0.0))
    charges_paid = float(_tr["commission"].sum()) if "commission" in _tr.columns else 0.0
    slippage_paid = float(_tr["slippage"].sum()) if "slippage" in _tr.columns else 0.0
    # NAUTILUS PATH: RegimeFeeModel charges notional x (COST_PCT + slip) as ONE commission, so the
    # `slippage` column is 0 and `commission` silently CONTAINS the slippage. Reporting that as
    # "charges 100% / slippage 0" would be plainly wrong, so decompose it back out at the known rate.
    if slippage_paid == 0.0 and charges_paid > 0 and {"qty", "px"}.issubset(_tr.columns):
        from qbeast_backtest.config import COST_PCT as _cp
        _notional = (_tr["qty"].abs() * _tr["px"])
        _ch = float((_notional * _cp).sum())
        if _ch < charges_paid * 0.999:                         # commission really is the bundled figure
            charges_paid, slippage_paid = _ch, charges_paid - _ch
    n_fills = int(len(_tr))
    frictions = tax_paid + charges_paid + slippage_paid
    turnover = float((_tr["qty"].abs() * _tr["px"]).sum()) if {"qty", "px"}.issubset(_tr.columns) else 0.0
    charges_bps = (charges_paid / turnover * 1e4) if turnover > 0 else 0.0
    slippage_bps = (slippage_paid / turnover * 1e4) if turnover > 0 else 0.0
    try:
        from qbeast_backtest.config import SLIPPAGE_MODE as _sm
        slip_label = f"{_sm} model"
    except Exception:                                          # noqa: BLE001 — older config
        slip_label = "per-stock liquidity model"

    kpis = [
        _card("Strategy", strat_name, None, strat_sub),
        _card("Start capital", f'Rs {INITIAL_CAPITAL:,.0f}', None, f'{nav.index[0].date()}'),
        _card("Final capital", f'Rs {s["final"]:,.0f}', s["final"] > sb["final"],
              f'{nav.index[-1].date()}'
              + (f' &middot; {s["final"]/INITIAL_CAPITAL:.1f}x' if INITIAL_CAPITAL else "")
              + f' &middot; index Rs {sb["final"]:,.0f}'),
        _card("CAGR (after tax)", f'{s["cagr"]:.2f}%', s["cagr"] > sb["cagr"], f'index {sb["cagr"]:.2f}%'),
        _card("CAGR (no tax)", f'{s_pre["cagr"]:.2f}%', None, 'gross of capital-gains tax'),
        _card("Ann. arith (after tax)", f'{s["ann_arith"]:.2f}%', None, 'geometric=FALSE (mean&times;252)'),
        _card("Ann. arith (no tax)", f'{s_pre["ann_arith"]:.2f}%', None, 'geometric=FALSE, gross'),
        _card("Sharpe", f'{s["sharpe"]:.2f}', s["sharpe"] > sb["sharpe"], f'index {sb["sharpe"]:.2f}'),
        _card("Calmar", f'{s["calmar"]:.3f}', s["calmar"] > sb["calmar"], f'index {sb["calmar"]:.3f}'),
        _card("Idle cash (avg)", f'{idle_pct:.0f}%', None, f'avg Rs {idle_rs:,.0f} in cash'),
        _card("Max drawdown", f'{s["maxdd"]:.1f}%', passed, f'index {sb["maxdd"]:.1f}%'),
        _card("Drawdown gate", "PASS" if passed else "FAIL", passed,
              "strategy must not draw down harder than the index"),
        # --- what the benchmark did, and what the three frictions actually cost ---------------------
        _card(f"{bench_label()} CAGR", f'{sb["cagr"]:.2f}%', None,
              f'buy &amp; hold the index &middot; maxDD {sb["maxdd"]:.1f}%'),
        # Denominators chosen to MEAN something over a multi-year compounding run: expressing cumulative
        # frictions as a % of START capital gives absurdities like "104% of start capital". Tax is shown
        # as the CAGR it actually cost; trading frictions as bps of the turnover that generated them.
        _card("Capital-gains tax paid", f'Rs {tax_paid:,.0f}', None,
              f'costs {s_pre["cagr"] - s["cagr"]:.2f}pp of CAGR &middot; FIFO, by sale date'),
        _card("Transaction charges", f'Rs {charges_paid:,.0f}', None,
              f'{charges_bps:,.0f} bps of Rs {turnover/1e7:,.1f} cr turnover &middot; {n_fills:,} fills'),
        _card("Slippage cost", f'Rs {slippage_paid:,.0f}', None,
              f'{slippage_bps:,.0f} bps of turnover &middot; {slip_label}'),
        _card("Total frictions", f'Rs {frictions:,.0f}', None,
              f'tax + charges + slippage &middot; {frictions / max(s["final"], 1) * 100:.1f}% of final NAV'),
    ]
    # A STRATEGY banner, not a gate banner. It used to announce the active trend gate, describe it,
    # and list the six others - all of it the removed strategy's design rather than anything about
    # this run.
    _pp = " &middot; ".join(f"{k} <b>{v}</b>" for k, v in (_STRAT.get("params") or {}).items())
    gate_banner = (f'<div style="background:#eef4fb;border:1px solid #cfe0f3;color:{NAVY};'
                   f'padding:10px 15px;border-radius:8px;margin:0 30px 8px;font-size:13px">'
                   f'<b>Strategy: {_strategy_name()}</b>'
                   + (f' &mdash; {_pp}.' if _pp else '.')
                   + f' Benchmark: <b>{bench_label()}</b> (from <code>INDEX_SYMBOL</code>). '
                   f'Choose the strategy with <code>--strategy</code>; register yours in '
                   f'<code>stage2_strategy/registry.py</code>.</div>')
    dd_note = "" if passed else (
        f'<div class="warn"><b>Drawdown gate FAILS.</b> The strategy&rsquo;s worst drawdown '
        f'({s["maxdd"]:.1f}%) is deeper than the index&rsquo;s ({sb["maxdd"]:.1f}%).</div>')
    gate_note = gate_banner + dd_note

    tabs = [("Overview", panel_overview(nav, nav_pre, bench, res["invested"])),
            ("Returns (tax)", panel_returns(s, s_pre, sb, led)),
            ("Regime map", panel_regime(idxc_disp, state, deployment, gate_extra)),
            # `level` is whatever curve the strategy published; absent, the tab is just
            # the benchmark candles. The gate overlay it replaced was strategy-specific.
            ("Index", panel_index_chart(
                idx_ohlc, level=(diag["level"] if (diag := getattr(res.get("strategy"),
                                 "diagnostics", None)) is not None
                                 and "level" in getattr(diag, "columns", []) else None))),
            ("Trend-gate comparison", panel_gate_compare(gate_extra, idxc_disp)),
            ("Year by year", panel_yearly(nav, bench, res["invested"])),
            ("Monthly returns", panel_monthly(nav)),
            ("Rebalance", panel_rebalance(res.get("rebalance_log", []))),
            ("Signal grid", panel_signals(res.get("mom"), res.get("memb"), res.get("closes"),
                                          res.get("rebalance_log", []))),
            ("Trend prediction", panel_prediction(pj)),
            ("Feature selection", panel_features(pj)),
            ("Trades", panel_trades(led)),
            ("Configuration", panel_config(led, s)),
            ("Logs", panel_logs())]

    # panel_index_chart and panel_gate_compare return "" here - both were gate-specific, and an
    # empty string would otherwise render as an empty tab.
    tabs = [(_n, _h) for _n, _h in tabs if str(_h).strip()]
    _write_page(OUT_DIR / "dashboard.html",
                f"{_strategy_title()} — {bench_label()}",
                f"momentum selection &middot; regime-conditioned deployment, {posture_txt} &middot; "
                "net of Indian taxes &amp; costs, next-day-open fills &middot; "
                "executed on <b>NautilusTrader v1.228.0</b>",
                kpis, gate_note, tabs)


def main() -> None:
    """Not the entry point - `qbeast-backtest` runs the pipeline and writes every report. This exists
    so the dashboard can be regenerated on its own during development."""
    from qbeast_backtest import pipeline, report
    report.write(pipeline.run())


if __name__ == "__main__":
    main()
