r"""
qbeast_exec.selftest — verification suite. Run:  python -m qbeast_exec.selftest

Every assertion has a hand-computed expected value, so a teammate can confirm the library agrees with
their own arithmetic before trusting it.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from .charges import ChargesCalculator, ChargesConfig, SlippageConfig, SlippageModel
from .report import Dashboard, drawdown_table, performance_stats
from .tax import TaxConfig, TaxEngine

OK, FAIL = [], []


def check(name, got, want, tol=1e-6):
    if isinstance(want, bool) or isinstance(got, (bool, np.bool_)):
        good = bool(got) == bool(want)
    elif isinstance(want, (int, float)):
        good = abs(float(got) - float(want)) <= tol
    else:
        good = got == want
    (OK if good else FAIL).append(name)
    print(f"  [{'PASS' if good else 'FAIL'}] {name}: got {got!r}, want {want!r}")


def test_tax():
    print("\n=== TAX ===")
    # 1. Pure STCG, OLD regime (sale before 23-Jul-2024): gain 100k @ 15% = 15,000
    t = TaxEngine()
    t.buy("A", "2024-01-10", 100, 1000.0)
    t.sell("A", "2024-06-10", 100, 2000.0)               # +100,000 short, old
    check("STCG old 15%", t.tax_for(2024), 15_000.0)

    # 2. Pure STCG, NEW regime (sale on/after 23-Jul-2024): 100k @ 20% = 20,000
    t = TaxEngine()
    t.buy("A", "2024-01-10", 100, 1000.0)
    t.sell("A", "2024-08-10", 100, 2000.0)
    check("STCG new 20%", t.tax_for(2024), 20_000.0)

    # 3. LTCG new: held >365d, gain 225,000, exemption 125,000 -> 100,000 @ 12.5% = 12,500
    t = TaxEngine()
    t.buy("A", "2023-01-10", 100, 1000.0)
    t.sell("A", "2024-08-10", 100, 3250.0)               # gain 225,000 long, new
    check("LTCG new 12.5% above 1.25L", t.tax_for(2024), 12_500.0)

    # 4. LTCG old: FY2023-24, exemption 100,000; gain 200,000 -> 100,000 @ 10% = 10,000
    t = TaxEngine()
    t.buy("A", "2022-01-10", 100, 1000.0)
    t.sell("A", "2023-06-10", 100, 3000.0)
    check("LTCG old 10% above 1.0L", t.tax_for(2023), 10_000.0)

    # 5. CROSS-BUCKET NETTING: pre-cutoff STCG gain +200k, post-cutoff STCG loss -50k.
    #    Net 150k; old-rate portion consumed first = min(200k,150k)=150k @15% = 22,500
    t = TaxEngine()
    t.buy("A", "2024-04-01", 100, 1000.0)
    t.sell("A", "2024-06-01", 100, 3000.0)               # +200,000 old
    t.buy("B", "2024-08-01", 100, 1000.0)
    t.sell("B", "2024-09-01", 100, 500.0)                # -50,000 new
    check("STCG cross-bucket netting", t.tax_for(2024), 22_500.0)

    # 6. FIFO split across lots AND across the 365-day boundary.
    #    Lot1 bought 2023-01-02 @100 (long by 2024-08-01), Lot2 bought 2024-05-01 @200 (short).
    #    Sell 200 @ 300 on 2024-08-01: long gain 100*(300-100)=20,000 ; short 100*(300-200)=10,000
    t = TaxEngine()
    t.buy("A", "2023-01-02", 100, 100.0)
    t.buy("A", "2024-05-01", 100, 200.0)
    out = t.sell("A", "2024-08-01", 200, 300.0)
    check("FIFO split long leg", out["long"], 20_000.0)
    check("FIFO split short leg", out["short"], 10_000.0)
    # tax: STCG 10,000@20% = 2,000 ; LTCG 20,000 - 125,000 exemption -> 0
    check("FIFO split total tax", t.tax_for(2024), 2_000.0)

    # 7. Net LOSS year pays nil
    t = TaxEngine()
    t.buy("A", "2024-08-01", 100, 1000.0)
    t.sell("A", "2024-09-01", 100, 500.0)
    check("net loss pays nil", t.tax_for(2024), 0.0)

    # 8. CARRY-FORWARD off (default): loss in FY1 does NOT reduce FY2 tax
    t = TaxEngine()
    t.buy("A", "2024-08-01", 100, 1000.0); t.sell("A", "2024-09-01", 100, 500.0)   # -50,000 FY2024
    t.buy("B", "2025-06-01", 100, 1000.0); t.sell("B", "2025-09-01", 100, 1500.0)  # +50,000 FY2025
    t.assess_all()
    check("no carry-forward: FY2025 taxed in full", t.assessed[2025]["tax"], 10_000.0)

    # 9. CARRY-FORWARD on: the FY2024 loss absorbs the FY2025 gain -> nil
    t = TaxEngine(TaxConfig(carry_forward_losses=True))
    t.buy("A", "2024-08-01", 100, 1000.0); t.sell("A", "2024-09-01", 100, 500.0)
    t.buy("B", "2025-06-01", 100, 1000.0); t.sell("B", "2025-09-01", 100, 1500.0)
    t.assess_all()
    check("carry-forward absorbs later gain", t.assessed[2025]["tax"], 0.0)

    # 10. Cess: 20% STCG on 100k = 20,000, +4% cess = 20,800
    t = TaxEngine(TaxConfig(cess=0.04))
    t.buy("A", "2024-08-01", 100, 1000.0); t.sell("A", "2024-09-01", 100, 2000.0)
    check("4% cess applied", t.tax_for(2024), 20_800.0)

    # 11. assess() fires once, only after FY end
    t = TaxEngine()
    t.buy("A", "2024-08-01", 100, 1000.0); t.sell("A", "2024-09-01", 100, 2000.0)
    check("no tax before FY end", t.assess("2025-03-30"), 0.0)
    check("tax due at FY end", t.assess("2025-03-31"), 20_000.0)
    check("not charged twice", t.assess("2025-06-30"), 0.0)

    # 12. FY boundary: 31-Mar-2025 is FY2024; 01-Apr-2025 is FY2025
    check("FY of 31-Mar", TaxEngine().fy("2025-03-31"), 2024)
    check("FY of 01-Apr", TaxEngine().fy("2025-04-01"), 2025)

    # 13. Overselling is refused
    t = TaxEngine(); t.buy("A", "2024-01-01", 10, 100.0)
    try:
        t.sell("A", "2024-02-01", 20, 110.0); check("oversell refused", False, True)
    except ValueError:
        check("oversell refused", True, True)

    # 14. HOLDING PERIOD = 12 CALENDAR MONTHS (s.2(42A)), not 365 days.
    #     "not more than 12 months" is SHORT-term, so long-term starts the day AFTER the anniversary.
    cfg = TaxConfig()
    check("exactly 12 months is SHORT-term", cfg.is_long_term("2023-03-01", "2024-03-01"), False)
    check("12 months + 1 day is LONG-term", cfg.is_long_term("2023-03-01", "2024-03-02"), True)
    # leap-year span: 365 days elapsed but the anniversary has not been reached
    check("leap-year 365d still SHORT-term", cfg.is_long_term("2023-03-01", "2024-02-29"), False)
    check("leap-day purchase clamps to 28-Feb", cfg.long_term_on_or_after("2024-02-29"),
          pd.Timestamp("2025-03-01"))
    check("month-end anniversary", cfg.long_term_on_or_after("2023-01-31"), pd.Timestamp("2024-02-01"))
    # explicit day-count override reproduces the legacy behaviour
    legacy = TaxConfig(ltcg_hold_days=365)
    check("day-count override: 365d = LONG", legacy.is_long_term("2023-03-01", "2024-02-29"), True)
    # inclusive boundary option
    inc = TaxConfig(ltcg_boundary_inclusive=True)
    check("inclusive boundary: 12m exactly = LONG", inc.is_long_term("2023-03-01", "2024-03-01"), True)
    # and it flows through to the TAX: same trade, two classifications, two rates
    a = TaxEngine(); a.buy("A", "2023-03-01", 100, 1000.0); a.sell("A", "2024-08-29", 100, 3000.0)
    check("held >12m -> LTCG 12.5% above 1.25L", a.tax_for(2024), (200_000 - 125_000) * 0.125)
    b = TaxEngine(); b.buy("A", "2023-03-01", 100, 1000.0); b.sell("A", "2024-02-29", 100, 3000.0)
    check("held <12m -> STCG 15% (old regime)", b.tax_for(2023), 200_000 * 0.15)


def test_charges():
    print("\n=== CHARGES ===")
    c = ChargesCalculator(ChargesConfig.equity_delivery())
    b = c.breakdown("BUY", 100, 1000.0)                  # turnover 100,000
    check("delivery BUY stt 0.1%", b["stt"], 100.0)
    check("delivery BUY stamp 0.015%", b["stamp_duty"], 15.0)
    check("delivery BUY exchange", b["exchange"], 2.97)
    check("delivery BUY sebi", b["sebi"], 0.1)
    check("delivery BUY gst 18%", b["gst"], round((0 + 2.97 + 0.1) * 0.18, 10), 1e-9)
    check("delivery BUY total", b["total"], 100 + 15 + 2.97 + 0.1 + (3.07 * 0.18), 1e-6)
    check("delivery BUY rate 0.1186%", round(b["rate"] * 100, 4), 0.1186, 1e-9)

    s = c.breakdown("SELL", 100, 1000.0)
    check("delivery SELL no stamp", s["stamp_duty"], 0.0)
    check("delivery SELL rate 0.1036%", round(s["rate"] * 100, 4), 0.1036, 1e-9)

    i = ChargesCalculator(ChargesConfig.equity_intraday())
    check("intraday BUY has no STT", i.breakdown("BUY", 100, 1000.0)["stt"], 0.0)
    check("intraday SELL stt 0.025%", i.breakdown("SELL", 100, 1000.0)["stt"], 25.0)

    d = ChargesCalculator(ChargesConfig.equity_delivery(dp_charge_flat_sell=15.0))
    check("DP charge on sell only", d.breakdown("SELL", 100, 1000.0)["dp_charge"], 15.0)
    check("DP charge not on buy", d.breakdown("BUY", 100, 1000.0)["dp_charge"], 0.0)

    bl = ChargesCalculator(ChargesConfig.blended(0.0011))
    check("blended 11bps", bl.breakdown("BUY", 100, 1000.0)["total"], 110.0)


def _synth_frames(n=400, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    out = {}
    for k, sym in enumerate(["LIQ", "MID", "ILL"]):
        px = 1000 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
        hi = px * (1 + abs(rng.normal(0, 0.008, n)))
        lo = px * (1 - abs(rng.normal(0, 0.008, n)))
        vol = np.full(n, [5e6, 5e5, 5e4][k], dtype=float)
        out[sym] = pd.DataFrame({"open": px, "high": np.maximum(hi, px), "low": np.minimum(lo, px),
                                 "close": px, "volume": vol}, index=idx)
    return out


def test_slippage():
    print("\n=== SLIPPAGE ===")
    fr = _synth_frames()
    m = SlippageModel(fr, SlippageConfig(mode="dynamic", seed=11))
    d = pd.Timestamp("2024-09-02")
    k = m.terms("LIQ", d, qty=100, price=1000.0)
    check("mu > 0", k["mu"] > 0, True)
    check("p in [0.5,1]", 0.5 <= k["p"] <= 1.0, True)
    check("A > 0", k["A"] > 0, True)
    # E[eps] must equal mu exactly (that is the whole design)
    check("E[eps] == mu", round(k["A"] * (2 * k["p"] - 1), 10), round(k["mu"], 10), 1e-9)

    # illiquid must cost more than liquid for the same order size
    kl = m.terms("LIQ", d, qty=50_000, price=1000.0)
    ki = m.terms("ILL", d, qty=50_000, price=1000.0)
    check("illiquid mu > liquid mu", ki["mu"] > kl["mu"], True)
    # size-awareness: bigger order costs more
    check("bigger order costs more", m.terms("MID", d, 500_000, 1000.)["mu"] >
          m.terms("MID", d, 100, 1000.)["mu"], True)
    # participation guard
    check("participation guard fires", m.terms("ILL", d, 10_000_000, 1000.)["feasible"], False)

    # two-sided: over many fills we must see BOTH adverse and favourable
    m2 = SlippageModel(fr, SlippageConfig(mode="dynamic", seed=5))
    eps = [m2.draw("MID", dt, "BUY", 100, 1000.0)
           for dt in fr["MID"].index[200:350]]
    check("two-sided: some favourable", any(e < 0 for e in eps), True)
    check("two-sided: some adverse", any(e > 0 for e in eps), True)

    # determinism: same inputs -> same draw
    a = SlippageModel(fr, SlippageConfig(seed=42)).draw("MID", d, "BUY", 100, 1000.0)
    b = SlippageModel(fr, SlippageConfig(seed=42)).draw("MID", d, "BUY", 100, 1000.0)
    check("deterministic given seed", a, b)

    # buy pays more, sell receives less, for the SAME adverse draw
    m3 = SlippageModel(fr, SlippageConfig(mode="fixed", fixed_rate=0.001))
    check("fixed BUY fills above ref", m3.fill_price("MID", d, "BUY", 100, 1000.0), 1001.0, 1e-9)
    check("fixed SELL fills below ref", m3.fill_price("MID", d, "SELL", 100, 1000.0), 999.0, 1e-9)

    cap = m.capacity(["LIQ", "MID", "ILL"], d, [1e7, 1e9], top_n=20)
    check("capacity table built", len(cap), 2)
    check("bigger portfolio costs more", cap.iloc[1]["mean_mu_pct"] > cap.iloc[0]["mean_mu_pct"], True)


def test_report():
    print("\n=== REPORT ===")
    nav = pd.Series([100, 95, 90, 95, 100, 90, 80, 90, 100],
                    index=pd.bdate_range("2024-01-01", periods=9))
    s = performance_stats(nav)
    check("max_dd", round(s["max_dd"], 2), -20.0)
    check("avg_dd_episode", round(s["avg_dd_episode"], 2), -15.0)
    check("n_dd_episodes", s["n_dd_episodes"], 2)
    check("pct_underwater", round(s["pct_underwater"], 1), 66.7)
    check("empty nav does not raise", np.isnan(performance_stats(pd.Series(dtype=float))["cagr"]), True)
    check("drawdown table rows", len(drawdown_table(nav)), 2)

    # --- ANNUALISATION: calendar time, not a bar count -------------------------------------------
    # `yrs = len(nav) / 252` is a bar count over a nominal constant, which is not elapsed time. NSE
    # runs ~247.8 sessions a year, so 252 understated the years by 1.69% and overstated every CAGR -
    # NIFTY 100 over 01-01-2019..29-05-2026 read 11.49% against a true 11.28%.
    # Exactly TWO calendar years of business days, doubling: the true CAGR is sqrt(2)-1 = 41.42%.
    bd = pd.bdate_range("2020-01-01", "2021-12-31")
    two = pd.Series(np.linspace(100.0, 200.0, len(bd)), index=bd)
    s2 = performance_stats(two)
    check("years are calendar years", round(s2["years"], 3), 2.0, tol=0.01)
    check("CAGR compounds over calendar years", round(s2["cagr"], 2), 41.42, tol=0.05)
    check("bars_per_yr is the MEASURED rate, not an assumption",
          round(s2["bars_per_yr"], 1), round(len(two) / s2["years"], 1))
    check("and it is not 252", abs(s2["bars_per_yr"] - 252) > 1, True)
    check("a bar count would have given a DIFFERENT answer",
          abs(len(two) / 252.0 - s2["years"]) > 0.01, True)
    # simple = total / years, uncompounded: 100% over 2 years = 50%/yr
    check("simple is uncompounded (total / years)", round(s2["simple"], 2), 50.0, tol=0.05)

    # ORDERING. At a MODEST return the arithmetic annualisation sits above the compounded CAGR (the
    # variance drag) and below the simple return. It is NOT a universal rule: CAGR compounds the daily
    # rate while ann_arith only multiplies it, so on the 41%/yr series above CAGR actually OVERTAKES
    # ann_arith (41.45 vs 34.8). Asserted on a realistic ~11%/yr series, which is where the reports
    # live - the real NIFTY 100 window gives CAGR 11.28 < ann_arith 12.21 < simple 16.30.
    rng = np.random.default_rng(4)
    bd3 = pd.bdate_range("2019-01-01", "2026-05-29")
    mod = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.00043, 0.009, len(bd3)))), index=bd3)
    s3 = performance_stats(mod)
    check("CAGR < ann_arith at a modest return", s3["cagr"] < s3["ann_arith"], True,
          )
    check("ann_arith < simple at a modest return", s3["ann_arith"] < s3["simple"], True)
    # A non-dated index must be REFUSED, not silently annualised on a bar count. This test is why the
    # first attempt's "RangeIndex fallback" was removed: resample() below would have failed anyway, so
    # the fallback only moved the error later and made it less clear.
    try:
        performance_stats(pd.Series(np.linspace(100.0, 200.0, 504)))
        check("a RangeIndex is refused with a clear message", False, True)
    except TypeError as e:
        check("a RangeIndex is refused with a clear message", "DatetimeIndex" in str(e), True)

    t = TaxEngine()
    t.buy("A", "2024-01-02", 100, 100.0); t.sell("A", "2024-06-03", 100, 150.0)
    html = Dashboard(nav, tax=t, charges=ChargesCalculator(),
                     slippage=SlippageModel(_synth_frames()), title="Selftest").to_html()
    check("dashboard renders", "<html" in html and "Selftest" in html, True)
    check("dashboard has tax tab", "capital-gains" in html, True)


def test_bands():
    print("\n=== PRICE BANDS / CIRCUIT LOCKS ===")
    from .bands import BandConfig, PriceBandModel

    idx = pd.bdate_range("2024-01-01", periods=300)

    def frame(close, hi=None, lo=None):
        c = pd.Series(close, index=idx[:len(close)], dtype=float)
        return pd.DataFrame({"open": c, "close": c,
                             "high": c if hi is None else pd.Series(hi, index=c.index, dtype=float),
                             "low": c if lo is None else pd.Series(lo, index=c.index, dtype=float)})

    # A 10%-band stock: normal +-2% noise, then days locked exactly at the band.
    # Build from RETURNS then cumprod, so inserting a lock cannot distort the NEXT day's return
    # (mutating prices in place does, which silently pushes the inferred band to 20%).
    rng = np.random.default_rng(0)
    rets = rng.normal(0, 0.02, 251)
    rets[200] = 0.10                                               # LOCKED UP at the band
    rets[210] = -0.10                                              # LOCKED DOWN at the band
    rets[220] = 0.0                                                # flat but ZERO move -> illiquid
    px = 100.0 * np.cumprod(1.0 + rets)
    hi, lo = px * 1.01, px * 0.99
    for i in (200, 210, 220):                                      # flat bars: high == low == close
        hi[i] = lo[i] = px[i]
    f = {"BANDED": frame(px, hi, lo)}
    m = PriceBandModel(f, BandConfig())
    d_up, d_dn, d_il = idx[200], idx[210], idx[220]
    check("band inferred = 10%", m.band("BANDED"), 0.10)
    check("locked UP detected", m.state("BANDED", d_up), "upper")
    check("locked DOWN detected", m.state("BANDED", d_dn), "lower")
    check("flat-but-no-move is NOT a lock", m.state("BANDED", d_il), "open")
    check("normal day is open", m.state("BANDED", idx[150]), "open")

    # the gate blocks only the side that cannot execute
    check("cannot BUY into upper lock", m.can_trade("BANDED", d_up, "BUY")[0], False)
    check("CAN sell into upper lock", m.can_trade("BANDED", d_up, "SELL")[0], True)
    check("cannot SELL into lower lock", m.can_trade("BANDED", d_dn, "SELL")[0], False)
    check("CAN buy into lower lock", m.can_trade("BANDED", d_dn, "BUY")[0], True)

    # check() records refusals for the audit trail
    m.check("BANDED", d_up, "BUY", 100); m.check("BANDED", d_dn, "SELL", 50)
    m.check("BANDED", idx[150], "BUY", 10)                          # allowed -> not recorded
    check("blocked audit trail", len(m.blocked_table()), 2)
    check("summary counts blocked buys", m.summary()["blocked_buys"], 1)

    # PENNY STOCK: at Rs 0.50 one Rs 0.05 tick IS a 10% move -> must NOT be called a circuit
    pen = np.full(260, 0.50)
    pen[200] = 0.55                                                 # one tick = +10%
    h = pen.copy(); l = pen.copy()
    mp = PriceBandModel({"PENNY": frame(pen, h, l)}, BandConfig())
    check("penny-stock tick move is NOT a lock", mp.state("PENNY", idx[200]), "open")

    # an unbanded (F&O) name: moves exceed every candidate band -> no band, never locked
    big = 100 * np.cumprod(1 + rng.normal(0, 0.09, 260))
    mb = PriceBandModel({"FNO": frame(big, big * 1.01, big * 0.99)}, BandConfig())
    check("no band inferred for wide-moving name", mb.band("FNO"), None)

    # unknown symbol / date is tradeable (never block on missing data)
    check("unknown symbol is tradeable", m.can_trade("NOSUCH", d_up, "BUY")[0], True)
    check("None date is tradeable", m.can_trade("BANDED", None, "BUY")[0], True)

    # TIER 2 clamp (off by default)
    check("clamp is a no-op by default", m.clamp("BANDED", 999.0, 100.0), 999.0)
    m2 = PriceBandModel(f, BandConfig(clamp_fills_to_band=True))
    check("clamp limits to +band", m2.clamp("BANDED", 999.0, 100.0), 110.0)
    check("clamp limits to -band", m2.clamp("BANDED", 1.0, 100.0), 90.0)

    # scan() surfaces the real locks
    check("scan finds exactly the 2 real locks", len(m.scan()), 2)


def test_mwcb():
    print("\n=== MARKET-WIDE CIRCUIT BREAKER ===")
    from .bands import MarketWideBreaker, MWCBConfig

    idx = pd.bdate_range("2024-01-01", periods=12)
    # Three engineered shocks: -11%, +16%, -21%. Each shock must PERSIST, not bounce back: a flat
    # series that drops to 89 and returns to 100 the next day contains a +12.4% recovery, which is
    # itself a trigger. Building the level cumulatively keeps one trigger per shock - the first draft
    # of this fixture produced six.
    shocks = {3: 0.89, 6: 1.16, 9: 0.79}
    c, lvl = [], 100.0
    for i in range(12):
        lvl *= shocks.get(i, 1.0)
        c.append(lvl)
    s = pd.Series(c, index=idx)
    mw = MarketWideBreaker(s, index_name="TEST")

    sc = mw.scan()
    check("mwcb: three triggers found", len(sc), 3)
    check("mwcb: -11% is a 10% breach", mw.state(idx[3])["level"], 0.10)
    check("mwcb: -11% direction", mw.state(idx[3])["direction"], "lower")
    check("mwcb: +16% is a 15% breach", mw.state(idx[6])["level"], 0.15)
    check("mwcb: +16% direction is upper", mw.state(idx[6])["direction"], "upper")

    # A -21% day must report the MOST SEVERE level breached, not the mildest. Reporting 10 here would
    # understate a halt-for-the-day as a 45-minute pause.
    check("mwcb: -21% reports level 20 not 10", mw.state(idx[9])["level"], 0.20)
    check("mwcb: level 20 halts the rest of the day", mw.state(idx[9])["rest_of_day"], True)

    # time-of-day changes the halt length, and 10% after 14:30 is no halt at all
    check("mwcb: 10% pre-13:00 halts 45 min", mw.halt_minutes(0.10, "pre1300"), 45)
    check("mwcb: 10% mid-session halts 15 min", mw.halt_minutes(0.10, "mid"), 15)
    check("mwcb: 10% after 14:30 does not halt", mw.halt_minutes(0.10, "post1430"), 0)

    # a quiet day is not a trigger, and carries no level
    q = mw.state(idx[1])
    check("mwcb: quiet day not triggered", q["triggered"], False)
    check("mwcb: quiet day has no level", q["level"] is None, True)

    # --- synthetic stress: the whole point of the class ---------------------------------------------
    st = mw.stress(idx[1], -0.20)
    check("mwcb: stress triggers", st["triggered"], True)
    check("mwcb: stress is flagged synthetic", st["synthetic"], True)
    check("mwcb: real days are not flagged synthetic", mw.state(idx[3])["synthetic"], False)
    check("mwcb: clear_stress removes the override", mw.clear_stress(), 1)
    check("mwcb: state reverts after clearing", mw.state(idx[1])["triggered"], False)

    g = mw.reopen_gap(idx[1], -0.20, recovery=0.5)
    check("mwcb: reopen gap halves the move", round(g["assumed_reopen_gap"], 6), -0.10)
    check("mwcb: reopen gap declares itself uncalibrated", g["calibrated"], False)

    s2 = mw.summary()
    check("mwcb: summary counts the 10% breaches", s2["breached_10pct"], 3)
    check("mwcb: summary counts the 20% breaches", s2["breached_20pct"], 1)
    check("mwcb: report mentions it does not gate", "does NOT gate" in mw.report(), True)

    # an empty/quiet series must not invent a trigger
    calm = MarketWideBreaker(pd.Series([100.0, 100.5, 100.2], index=idx[:3]), index_name="CALM")
    check("mwcb: calm series has no triggers", len(calm.scan()), 0)


def test_staircase():
    """The v2.4.1 tradability gate: both directions, all sides, evidence-based untagging."""
    print("\n=== STAIRCASE (circuit flags v2.4.1) ===")
    from .bands import StaircaseConfig, StaircaseFlag

    def ladder(direction="up", n_ladder=8, n_after=8, band=0.10, vol=5e4):
        n_pre = 40
        idx = pd.bdate_range("2023-01-02", periods=n_pre + n_ladder + n_after)
        rng = np.random.default_rng(7)
        rows, px = [], 100.0
        for i, d in enumerate(idx):
            if n_pre <= i < n_pre + n_ladder:           # locked at the band, thin
                px *= (1 + band) if direction == "up" else (1 - band)
                rows.append((d, px, px, px, px, vol * 0.2))
            else:
                px *= 1 + rng.normal(0, 0.012)
                rows.append((d, px, px * 1.01, px * 0.99, px, vol))
        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        return df.set_index("date"), idx

    # ---- the two-direction rule: this is what 2.4.1 adds over 2.4.0's defaults -----------------
    for direction, label in (("up", "CLIMBING"), ("down", "FALLING")):
        df, idx = ladder(direction)
        sc = StaircaseFlag({"LAD": df}, StaircaseConfig())
        tagged = [d for d in idx if sc.flagged("LAD", d)]
        check(f"staircase: a {direction}ward ladder is tagged", len(tagged) > 0, True)
        d = tagged[len(tagged) // 2]
        check(f"staircase: {label} direction reported", sc.direction("LAD", d), label)
        check(f"staircase: {label} BUY refused", sc.check("LAD", d, "BUY", 100), False)
        # THE RULE THAT CHANGED. The previous test asserted the opposite - "SELL allowed (exits stay
        # open)" - which is why a backtest built on it was cheaper than reality: a ladder locked at
        # its band has no bid to sell into.
        check(f"staircase: {label} SELL refused too", sc.check("LAD", d, "SELL", 100), False)
        check(f"staircase: {label} EXIT refused too", sc.can_exit("LAD", d), False)

    # ---- untagging is evidence-based, not a window expiring -------------------------------------
    df, idx = ladder()
    thin = df.copy()
    thin.iloc[-8:, thin.columns.get_loc("volume")] = 10.0        # quiet prices, no volume
    sc_thin = StaircaseFlag({"LAD": thin}, StaircaseConfig())
    sc_norm = StaircaseFlag({"LAD": df}, StaircaseConfig())
    check("staircase: no-volume days do NOT untag", sc_thin.flagged("LAD", idx[-1]), True)
    check("staircase: normal days with volume DO untag", sc_norm.flagged("LAD", idx[-1]), False)

    # ---- a calm name is never touched ------------------------------------------------------------
    cal = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e6},
                       index=idx)
    sc = StaircaseFlag({"CALM": cal}, StaircaseConfig())
    check("staircase: calm name never flagged", sc.flagged("CALM", idx[-1]), False)
    check("staircase: calm BUY allowed", sc.check("CALM", idx[-1], "BUY", 100), True)
    check("staircase: calm SELL allowed", sc.check("CALM", idx[-1], "SELL", 100), True)

    # ---- the refusal record ----------------------------------------------------------------------
    df, idx = ladder()
    sc = StaircaseFlag({"LAD": df}, StaircaseConfig())
    d = [x for x in idx if sc.flagged("LAD", x)][2]
    sc.check("LAD", d, "BUY", 100)
    sc.check("LAD", d, "SELL", 100)
    b = sc.blocked_table()
    check("staircase: both refusals recorded", len(b), 2)
    check("staircase: sides named", sorted(b["side"].tolist()), ["BUY", "SELL"])
    check("staircase: the gate reason is carried", b.iloc[0]["gate"].startswith("STAIRCASE"), True)
    s = sc.summary()
    check("staircase: summary states the version", s["version"], "2.4.1")
    check("staircase: summary counts both sides", (s["refused_buys"], s["refused_sells"]), (1, 1))
    check("staircase: a full gate reports no relaxation", "RELAXED" in s, False)

    # ---- the superseded knobs are refused BY NAME ------------------------------------------------
    # A parameter accepted and then ignored is how a configured risk control stops working.
    try:
        StaircaseConfig(window=20, min_hits=3, min_run=3)
        check("staircase: V1 knobs refused", False, True)
    except TypeError as e:
        check("staircase: V1 knobs refused by name",
              all(k in str(e) for k in ("window", "min_hits", "min_run")), True)

    # ---- and the V1 rules are still available, for reconciling old numbers -----------------------
    from .bands import StaircaseConfigV1, StaircaseFlagV1
    # V1 infers the band from the price history itself, so it needs SCATTERED band-sized days before
    # the ladder to have a band to compare against - the ladder alone is not enough. The v2.4.1
    # fixture above does not need them, which is itself a difference worth knowing: V1 silently sees
    # nothing on a name whose band it cannot infer.
    n1 = 300
    i1 = pd.bdate_range("2023-01-02", periods=n1)
    mv = np.random.default_rng(7).normal(0.0, 0.015, n1)
    for k in (40, 77, 120, 165, 210, 240):
        mv[k] = 0.10
    mv[n1 - 5:] = 0.10
    s1 = pd.Series(100.0 * np.cumprod(1.0 + np.r_[0.0, mv[1:]]), index=i1)
    f1 = {"LAD": pd.DataFrame({"open": s1, "high": s1, "low": s1, "close": s1, "volume": 1e4},
                              index=i1)}
    v1 = StaircaseFlagV1(f1, StaircaseConfigV1(window=20, min_hits=3, min_run=3))
    check("staircase V1: still constructible for reconciliation", v1.flagged("LAD", i1[-1]), True)
    check("staircase V1: and still has its OLD rule (sells allowed)",
          v1.check("LAD", i1[-1], "SELL", 100), True)
    check("staircase V1: which is exactly what v2.4.1 changed",
          StaircaseFlag(f1, StaircaseConfig()).check("LAD", i1[-1], "SELL", 100), False)


def test_survivorship():
    print("\n=== SURVIVORSHIP ===")
    import tempfile
    from pathlib import Path as _P

    from .survivorship import Membership, SurvivorshipConfig, assert_no_look_ahead, normalise

    check("normalise strips and uppers", normalise(" hdfc bank "), "HDFCBANK")

    d = _P(tempfile.mkdtemp())
    f = d / "test_membership.csv"
    f.write_text("effective_date,symbols\n"
                 "2016-04-29,\"AAA,BBB,CCC\"\n"
                 "2017-04-28,\"BBB,CCC,DDD\"\n"
                 "2018-04-27,\"CCC,DDD,EEE\"\n", encoding="utf-8")

    m = Membership(f, available={"AAA", "BBB", "CCC", "DDD"})
    check("survivorship: enabled", m.enabled, True)
    check("survivorship: 3 reconstitutions", len(m.records), 3)
    check("survivorship: first date", str(m.first_date.date()), "2016-04-29")

    # as-of lookup: the list in force on that date, not the next one
    check("survivorship: on the effective date", sorted(m.eligible("2016-04-29")), ["AAA", "BBB", "CCC"])
    check("survivorship: between dates holds", sorted(m.eligible("2017-01-01")), ["AAA", "BBB", "CCC"])
    check("survivorship: after the 2nd", sorted(m.eligible("2017-06-01")), ["BBB", "CCC", "DDD"])

    # EEE is a real constituent from 2018 but we have no prices -> excluded, and counted as missing
    check("survivorship: unpriced member excluded", sorted(m.eligible("2019-01-01")), ["CCC", "DDD"])
    check("survivorship: members() ignores prices", sorted(m.members("2019-01-01")), ["CCC", "DDD", "EEE"])
    check("survivorship: missing is reported", sorted(m.missing()), ["EEE"])

    # coverage quantifies the PARTIAL fix
    cov = m.coverage()
    check("survivorship: coverage rows", len(cov), 3)
    check("survivorship: coverage 2016 = 3/3", float(cov.iloc[0]["coverage_pct"]), 100.0)
    check("survivorship: coverage 2018 = 2/3", round(float(cov.iloc[2]["coverage_pct"]), 2), 66.67, tol=0.01)

    # look-ahead: a window starting before the first reconstitution must be detectable, not just printed
    a = m.audit(start="2010-01-01", end="2020-01-01")
    check("survivorship: look-ahead flagged", a["look_ahead"], True)
    check("survivorship: look-ahead days", a["look_ahead_days"], 2310)
    check("survivorship: no look-ahead when in range", m.audit(start="2017-01-01")["look_ahead"], False)

    raised = False
    try:
        assert_no_look_ahead(m, "2010-01-01")
    except ValueError:
        raised = True
    check("survivorship: assert_no_look_ahead raises", raised, True)

    # a missing file must RAISE, not quietly disable the control
    raised = False
    try:
        Membership(d / "does_not_exist.csv")
    except FileNotFoundError:
        raised = True
    check("survivorship: missing file raises", raised, True)
    check("survivorship: strict=False allows it",
          Membership(d / "nope.csv", config=SurvivorshipConfig(strict=False)).enabled, False)

    # pass-through mode: no path -> everything available is eligible, and it SAYS it is biased
    pt = Membership(None, available={"AAA", "ZZZ"})
    check("survivorship: pass-through eligible", sorted(pt.eligible("2020-01-01")), ["AAA", "ZZZ"])
    check("survivorship: pass-through not enabled", pt.enabled, False)
    check("survivorship: pass-through report warns", "SURVIVORSHIP BIASED" in pt.report(), True)

    # aliases map feed spellings onto our filenames
    f2 = d / "alias_membership.csv"
    f2.write_text("effective_date,symbols\n2020-01-01,\"ZOMATO,NIFTY,AAA\"\n", encoding="utf-8")
    m2 = Membership(f2)
    check("survivorship: alias applied", "ETERNAL" in m2.members("2020-06-01"), True)
    check("survivorship: junk dropped", "NIFTY" in m2.members("2020-06-01"), False)


def test_universe_report():
    print("\n=== UNIVERSE REPORT ===")
    import tempfile
    from pathlib import Path as _P

    from .universe import UniverseReport

    root = _P(tempfile.mkdtemp())
    a = UniverseReport(root, "SmallCap250")
    check("universe: name normalised", a.universe, "smallcap250")
    check("universe: folder created", a.dir.exists(), True)
    check("universe: path() nests", a.path("sub", "x.csv").parent.name, "sub")

    a.save({"cagr": 24.5, "maxdd": -25.4, "sharpe": 1.37, "final": 8_908_515},
           benchmark={"cagr": 15.4, "maxdd": -60.8}, window=("2016-04-29", "2026-05-29"), gate="regime")
    rec = a.load()
    check("universe: excess computed", round(rec["excess_cagr_pp"], 2), 9.10)
    check("universe: dd_better positive when shallower", round(rec["dd_better_pp"], 2), 35.40)
    check("universe: meta kept", rec["gate"], "regime")
    check("universe: window stored", rec["window_start"], "2016-04-29")

    b = UniverseReport(root, "nifty100")
    b.save({"cagr": 14.0, "maxdd": -30.0}, benchmark={"cagr": 12.0, "maxdd": -55.0})
    df = UniverseReport.compare(root)
    check("universe: two rows", len(df), 2)
    check("universe: excess column leads", list(df.columns)[0], "excess_cagr_pp")
    check("universe: indexed by universe", sorted(df.index), ["nifty100", "smallcap250"])

    # a universe with no run must be ABSENT, not a row of zeros
    UniverseReport(root, "midcap150")
    check("universe: unrun universe excluded", "midcap150" in UniverseReport.compare(root).index, False)
    check("universe: table carries the caveat", "NOT" in UniverseReport.table(root), True)
    check("universe: empty root is empty", UniverseReport.compare(root / "nope").empty, True)


def test_reprice():
    """reprice_to_open had NO tests, which is how the order book kept stale prices for weeks while the
    trade book beside it was correct. These pin the behaviour both books depend on."""
    print("\n=== REPRICE TO OPEN ===")
    import pandas as pd

    from .books import reprice_to_open

    opens = pd.DataFrame({"AAA": [100.0, 110.0], "BBB": [50.0, 55.0]},
                         index=pd.to_datetime(["2024-01-01", "2024-01-02"]))

    def frame():
        return pd.DataFrame({
            "time": pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-02"]),
            "symbol": ["AAA", "BBB", "ZZZ"],          # ZZZ is absent from the panel
            "px": [99.0, 55.0, 7.0],                  # AAA stale, BBB already right, ZZZ unknown
            "filled_qty": [10, 10, 10],
        })

    df, n = reprice_to_open(frame(), "time", "px", opens)
    check("reprice: only the stale row moves", n, 1)
    check("reprice: stale -> fill-day open", float(df.loc[0, "px"]), 110.0)
    check("reprice: already-correct untouched", float(df.loc[1, "px"]), 55.0)
    check("reprice: symbol absent from panel keeps its price", float(df.loc[2, "px"]), 7.0)

    # no panel -> a no-op, never a crash and never a silent zero
    df, n = reprice_to_open(frame(), "time", "px", None)
    check("reprice: opens=None is a no-op", (n, float(df.loc[0, "px"])), (0, 99.0))

    # a missing date column must RAISE, not quietly reprice nothing
    try:
        reprice_to_open(frame().rename(columns={"time": "ts"}), "time", "px", opens)
        check("reprice: missing date col raises", False, True)
    except KeyError:
        check("reprice: missing date col raises", True, True)

    # tz-aware input (Nautilus stamps UTC) must still match a tz-naive panel
    tz = frame()
    tz["time"] = tz["time"].dt.tz_localize("UTC")
    df, n = reprice_to_open(tz, "time", "px", opens)
    check("reprice: tz-aware timestamps still match", (n, float(df.loc[0, "px"])), (1, 110.0))

    # THE ORDER-BOOK CASE: an unfilled order's avg_px is 0 and must stay 0, or the book invents a trade
    unf = frame()
    unf.loc[0, ["px", "filled_qty"]] = [0.0, 0]
    df, n = reprice_to_open(unf, "time", "px", opens, mask=unf["filled_qty"] > 0)
    check("reprice: unfilled order keeps avg_px 0", float(df.loc[0, "px"]), 0.0)
    check("reprice: mask excludes it from the count", n, 0)
    # and WITHOUT the mask it would be stamped with a price -- the bug the mask exists to prevent
    df, n = reprice_to_open(unf.copy(), "time", "px", opens)
    check("reprice: unmasked would invent a price (why mask exists)", float(df.loc[0, "px"]), 110.0)


def test_deferred_submit():
    """The T -> T+1 invariant: NOTHING is ever submitted on the bar it was decided on.

    This is the cheapest test in the suite and it guards the most expensive bug class there is. A
    same-bar submit makes the decision read a price it then trades at -- a look-ahead that shows up as
    free alpha and nothing else.
    """
    print("\n=== DEFERRED SUBMIT (T -> T+1) ===")
    from .nautilus import DeferredSubmit

    d = DeferredSubmit()
    check("defer: starts empty", (d.waiting, list(d.release())), (False, []))

    d.queue("2024-01-01", {"AAA": 0.5})
    check("defer: queued but waiting", d.waiting, True)
    check("defer: NOTHING released on the queueing bar", len(list(DeferredSubmit().release())), 0)

    got = list(d.release())                              # the NEXT bar
    check("defer: released on the next bar", got, [("2024-01-01", {"AAA": 0.5})])
    check("defer: cleared after release", d.waiting, False)
    check("defer: a second release yields nothing", list(d.release()), [])
    check("defer: release counter", d.released, 1)

    # an EMPTY target means FLATTEN TO CASH and must survive. Dropping falsy targets once meant the
    # engine was never told to de-risk in a downtrend: drawdown went from -17.5% to -35%.
    d.queue("2024-01-02", {})
    check("defer: empty target is queued, not dropped", d.waiting, True)
    check("defer: empty target released intact", list(d.release()), [("2024-01-02", {})])

    # None is a programming error, not "flatten" -- it must raise rather than silently do nothing
    try:
        d.queue("2024-01-03", None)
        check("defer: None target raises", False, True)
    except ValueError:
        check("defer: None target raises", True, True)

    # re-queueing before release replaces, so a day cannot submit twice
    d.queue("2024-01-04", {"A": 1.0})
    d.queue("2024-01-04", {"B": 1.0})
    check("defer: re-queue replaces", list(d.release()), [("2024-01-04", {"B": 1.0})])


def test_ledger():
    """The ported ledger: cash, FIFO, yield, and the gross/net pair."""
    print("\n=== LEDGER ===")
    import pandas as pd

    from .ledger import Ledger, apply_fill, replay_fills

    cal = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
    closes = pd.DataFrame({"AAA": [100.0, 110.0, 120.0]}, index=cal)

    led = Ledger(100_000.0, cash_yield=0.0, tax=False)
    apply_fill(led, cal[0], "AAA", 100, 100.0, 50.0)
    check("ledger: cash after buy", round(led.cash, 2), round(100_000 - 100 * 100 - 50, 2))
    check("ledger: position opened", led.pos["AAA"], 100)
    nav = led.mark(cal[0], closes.loc[cal[0]])
    check("ledger: NAV = cash + mtm", round(nav, 2), round(led.cash + 100 * 100.0, 2))

    apply_fill(led, cal[1], "AAA", -100, 110.0, 50.0)
    check("ledger: position closed removes the key", "AAA" in led.pos, False)
    check("ledger: realised gain recorded", round(led.trades[-1]["realized_short"], 2), 1000.0)

    # cash yield accrues on idle cash, and only on idle cash
    y = Ledger(100_000.0, cash_yield=0.252, tax=False)       # 0.252/252 = exactly 0.001/day
    y.mark(cal[0], closes.loc[cal[0]])
    check("ledger: one day of yield", round(y.interest, 4), round(100_000 * 0.001, 4))

    # gross vs net on IDENTICAL fills: the only difference may be the tax deduction
    fills = [dict(ts=cal[0], symbol="AAA", qty=100, px=100.0, comm=50.0),
             dict(ts=cal[1], symbol="AAA", qty=-100, px=110.0, comm=50.0)]
    net = replay_fills(fills, closes, cal, 100_000.0, verbose=False)
    gross = replay_fills(fills, closes, cal, 100_000.0, tax=False, verbose=False)
    check("ledger: same trades both runs", (len(net.trades), len(gross.trades)), (2, 2))
    check("ledger: gross NAV >= net NAV",
          gross.equity_curve().iloc[-1] >= net.equity_curve().iloc[-1], True)

    # the reprice path, through replay_fills rather than the helper
    opens = pd.DataFrame({"AAA": [100.0, 999.0, 120.0]}, index=cal)
    # a realistic commission, not 0.0: a zero would be LESS than the recomputed statutory charges and
    # trip the schedule-mismatch warning, printing an alarming line in an otherwise clean test run.
    rp = replay_fills([dict(ts=cal[1], symbol="AAA", qty=10, px=110.0, comm=25.0)],
                      closes, cal, 100_000.0, opens=opens, verbose=False)
    check("ledger: replay repriced the fill to the open", round(rp.trades[0]["px"], 2), 999.0)




# test_dashboard() was REMOVED WITH qbeast_exec/dashboard.py - it rendered that
# module's panels. This copy is a subset of the canonical library; see
# fix_to_send_exec.py, and do not restore it with a plain file copy.


def main() -> int:
    print("qbeast_exec selftest")
    test_tax(); test_charges(); test_slippage(); test_report(); test_bands(); test_mwcb()
    test_staircase(); test_survivorship(); test_universe_report(); test_reprice()
    test_deferred_submit(); test_ledger()
    print(f"\n{len(OK)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
