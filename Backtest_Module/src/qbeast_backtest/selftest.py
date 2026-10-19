r"""
Backtest_Modular/selftest.py
============================
CONTRACT CONFORMANCE. Verifies that the three boundaries actually bind - because a boundary nobody
checks is a convention, and conventions get bypassed.

Runs offline on synthetic data. The arithmetic is tested by qbeast_exec's own 211 tests and by
`reconcile.py`, which proves the whole pipeline still reproduces Backtest_Core to the rupee. What is
tested here is the thing those two cannot test: that a MISBEHAVING strategy is caught.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE / "shared"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import numpy as np                                               # noqa: E402
import pandas as pd                                              # noqa: E402

from qbeast_backtest.contracts import ExecutionResult, MarketData, StrategyOutput  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok ' if cond else 'FAIL'}] {name}" + (f"   {detail}" if detail and not cond else ""))


def _md(n=600, window=200, warmup=400, elig=None):
    """A MarketData with `warmup` sessions of history before a `window`-session calendar."""
    idx = pd.bdate_range("2020-01-01", periods=n)
    syms = [f"S{i:02d}" for i in range(25)]
    rng = np.random.default_rng(3)
    px = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, (n, len(syms))), axis=0)),
                      index=idx, columns=syms)
    cal = idx[-window:]
    bench = px.mean(axis=1)
    rebals = [cal[0], cal[len(cal) // 2]]
    e = {t: frozenset(elig if elig is not None else syms) for t in rebals}
    return MarketData(calendar=cal, opens=px.reindex(cal) * 0.999, closes=px.reindex(cal),
                      volumes=pd.DataFrame(1e6, index=cal, columns=syms),
                      benchmark=bench.reindex(cal), signal_index=bench,
                      eligible=e, rebalance_dates=rebals,
                      history_closes=px, history_volumes=pd.DataFrame(1e6, index=idx, columns=syms),
                      warmup_bars=warmup, universe="test")


# ---------------------------------------------------------------------------------------------------
def test_marketdata():
    print("\n-- stage 1 -> 2 : MarketData --")
    md = _md()
    check("a well-formed MarketData validates", md.validate() == [], str(md.validate()))

    # THE WARM-UP GUARD. This is the bug the contract shipped with: a window-only MarketData cost
    # 371 days of momentum warm-up and a third of the NAV, silently.
    thin = _md(n=250, window=200, warmup=400)
    probs = thin.validate()
    check("too little pre-window history is REFUSED",
          any("history before the window" in p for p in probs), str(probs))
    check("and the message says how much is missing",
          any("needs 400" in p for p in probs), str(probs))

    import dataclasses
    empty = dataclasses.replace(md, history_closes=pd.DataFrame())
    check("an empty history is refused",
          any("history_closes is empty" in p for p in empty.validate()))

    # A MISSING eligibility set must be refused, not defaulted to the whole universe - defaulting
    # would silently restore every name the staircase just removed.
    gapped = dataclasses.replace(md, eligible={md.rebalance_dates[0]: frozenset({"S00"})})
    check("a rebalance with no eligible set is refused",
          any("no eligibility set" in p for p in gapped.validate()), str(gapped.validate()))


def test_strategyoutput():
    print("\n-- stage 2 -> 3 : StrategyOutput --")
    md = _md()
    t = md.rebalance_dates[0]

    good = StrategyOutput(targets={t: {"S00": 0.4, "S01": 0.4}}, name="t")
    check("a well-formed output validates", good.validate(md, cash_buffer=0.97) == [],
          str(good.validate(md, cash_buffer=0.97)))

    lev = StrategyOutput(targets={t: {"S00": 0.8, "S01": 0.8}}, name="t")
    check("weights above the cash buffer are refused (leverage)",
          any("leverage" in p for p in lev.validate(md, cash_buffer=0.97)))

    neg = StrategyOutput(targets={t: {"S00": -0.2}}, name="t")
    check("a negative weight is refused (long-only)",
          any("negative weight" in p for p in neg.validate(md)))

    off = StrategyOutput(targets={pd.Timestamp("1999-01-04"): {"S00": 0.5}}, name="t")
    check("a target off the trading calendar is refused",
          any("not a session" in p for p in off.validate(md)))

    # THE ONE THAT MAKES THE STAIRCASE BINDING. A strategy that ranks outside the eligible set has
    # bypassed survivorship or the staircase gate, and must not reach execution.
    md2 = _md(elig=["S00", "S01", "S02"])
    t2 = md2.rebalance_dates[0]
    stray = StrategyOutput(targets={t2: {"S00": 0.3, "S19": 0.3}}, name="t")
    probs = stray.validate(md2)
    check("a name OUTSIDE the eligible set is refused", any("outside the eligible set" in p
                                                            for p in probs), str(probs))
    check("and the message names the gate that was bypassed",
          any("staircase" in p for p in probs), str(probs))

    # {} is a flatten instruction and must be legal; it is NOT the same as a missing key.
    flat = StrategyOutput(targets={t: {}}, name="t")
    check("an empty target dict (flatten to cash) is legal", flat.validate(md) == [])


def test_executionresult():
    print("\n-- stage 3 output : ExecutionResult --")
    idx = pd.bdate_range("2024-01-01", periods=50)
    nav = pd.Series(np.linspace(100, 120, 50), index=idx)
    ok = ExecutionResult(nav=nav, nav_pretax=nav, invested=nav * 0, benchmark=nav, ledger=None)
    check("a well-formed result validates", ok.validate() == [])
    bad = ExecutionResult(nav=pd.Series([100.0, -5.0], index=idx[:2]), nav_pretax=nav,
                          invested=nav * 0, benchmark=nav, ledger=None)
    check("a non-positive NAV is refused", any("non-positive" in p for p in bad.validate()))


def test_tradability():
    print("\n-- stage 1 : the staircase actually narrows the pool --")
    from stage1_data.tradability import TradabilityGate

    idx = pd.bdate_range("2024-01-01", periods=60)
    syms = ["AAA", "BBB", "CCC"]
    closes = pd.DataFrame(100.0, index=idx, columns=syms)

    class FakeStair:
        """Stands in for StaircaseFlag: BBB is laddering, the others are not."""
        def flagged_symbols(self, date):
            return ["BBB"]

    g = TradabilityGate(closes, {}, staircase=FakeStair(), exclude_flagged=True)
    e = g.eligible_on(idx[-1], closes.iloc[-1])
    check("a flagged name is REMOVED from the pool", "BBB" not in e, str(sorted(e)))
    check("unflagged names survive", {"AAA", "CCC"} <= set(e))
    check("the removal is recorded for the log", "BBB" in
          g.report.excluded_from_pool.get(idx[-1], frozenset()))

    g2 = TradabilityGate(closes, {}, staircase=FakeStair(), exclude_flagged=False)
    check("exclude_flagged=False leaves the pool alone (for the A/B)",
          "BBB" in g2.eligible_on(idx[-1], closes.iloc[-1]))

    # A gate that cannot answer must NOT silently pass everything - that is the failure this module
    # exists to end.
    class BrokenStair:
        def flagged_symbols(self, date):
            raise RuntimeError("feed down")

    g3 = TradabilityGate(closes, {}, staircase=BrokenStair(), exclude_flagged=True)
    e3 = g3.eligible_on(idx[-1], closes.iloc[-1])
    check("a broken gate is announced, not silently permissive", len(e3) == 3)

    # a name with no price cannot be ranked or sized; leaving it in makes top-N quietly short
    c2 = closes.copy(); c2.loc[idx[-1], "CCC"] = np.nan
    g4 = TradabilityGate(c2, {}, staircase=None)
    check("an unpriced name is dropped from the pool",
          "CCC" not in g4.eligible_on(idx[-1], c2.iloc[-1]))


def test_registry_and_shape():
    """With no strategy shipped, the registry must REFUSE rather than fall back."""
    print(chr(10) + "-- registry (framework-only build) --")
    from qbeast_backtest.stage2_strategy import get, registry

    # Tested by EMPTYING the registry here, not by asserting it is empty on disk: an assertion about
    # the shipping state starts failing the moment the recipient adds their strategy, which is the
    # normal case. What must hold forever is the BEHAVIOUR - an empty registry refuses and explains,
    # instead of falling back to some leftover class.
    _saved, _sdef = dict(registry.REGISTRY), registry.DEFAULT
    try:
        registry.REGISTRY.clear()
        registry.DEFAULT = None
        try:
            get()
            check("an empty registry refuses", False, "it returned a strategy")
        except KeyError as e:
            check("an empty registry refuses rather than falling back",
                  "no strategies are registered" in str(e))
            check("and the message says what to do about it", "HANDOFF" in str(e))
        try:
            get("nope")
            check("an unknown name refuses", False, "it returned a strategy")
        except KeyError:
            check("an unknown name refuses too", True)
    finally:
        registry.REGISTRY.update(_saved)
        registry.DEFAULT = _sdef
    check("the registry was restored", registry.REGISTRY == _saved)

def _raises(fn, exc) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:                                            # noqa: BLE001
        return False
    return False


def test_report_adapters():
    """What a run writes, and the translation the dashboard reads.

    An adapter that returns the wrong shape here costs an EMPTY DASHBOARD TAB, not an exception -
    which is why these are tests and not a visual check."""
    print(chr(10) + "-- report adapters --")
    from qbeast_backtest import report as R
    from qbeast_backtest.contracts import StrategyOutput

    cal = pd.bdate_range("2024-01-01", periods=60)
    reb = [cal[0], cal[20], cal[40]]
    out = StrategyOutput(
        targets={reb[0]: {"A": .5, "B": .47}, reb[1]: {}, reb[2]: {"B": .5, "C": .47}},
        exposure=pd.Series({reb[0]: 0.97, reb[1]: 0.0, reb[2]: 0.6}),
        posture=pd.Series({reb[0]: "full", reb[1]: "cash", reb[2]: "sized"}), name="t")

    # ---- the stats aliases ----------------------------------------------------------------------
    # The dashboard was written against the monolith's short key names; the shared library uses long
    # ones. Same values - if these ever diverge, every figure in the dashboard is quoting a different
    # number from the result table.
    nav = pd.Series(1e5 * np.exp(np.cumsum(
        np.random.default_rng(5).normal(4e-4, .01, 700))),
        index=pd.bdate_range("2021-01-04", periods=700))
    s = R.stats(nav)
    check(f"all {len(R._ALIAS)} stats aliases carry the library's value",
          all(s[short] == s[long] for short, long in R._ALIAS.items()))
    check("and the long names are still there", {"max_dd", "years", "cagr"} <= set(s))

    # ---- the rebalance log, derived from the decisions --------------------------------------------
    log = R._rebalance_log(out)
    check("one row per decision session", len(log) == 3, f"{len(log)}")
    check("names held per row", [e["n"] for e in log] == [2, 0, 2], f"{[e['n'] for e in log]}")
    check("added/dropped come from consecutive decisions",
          [e["added"] for e in log] == [2, 0, 2] and [e["dropped"] for e in log] == [0, 2, 0])
    # An EMPTY target book is a FLATTEN, not a missing row. 41 of the reference run's 117 sessions are
    # flattens, so collapsing them would hide a third of the record.
    check("an empty book reads as a flatten, not a gap", log[1]["posture"] == "cash"
          and log[1]["deploy"] == 0.0 and log[1]["n"] == 0)
    check("the table carries what panel_rebalance reads",
          {"date", "posture", "deploy", "n", "added", "dropped", "names"} <= set(log[0]))

    bare = R._rebalance_log(StrategyOutput(targets={reb[0]: {"A": 1.0}, reb[1]: {}}, name="t"))
    check("posture is derived when the strategy publishes none",
          [e["posture"] for e in bare] == ["full", "cash"], f"{[e['posture'] for e in bare]}")
    check("no decisions at all gives an empty log, not a crash",
          R._rebalance_log(StrategyOutput(targets={}, name="t")) == []
          and R._rebalance_log(None) == [])

    # ---- the eligible view ------------------------------------------------------------------------
    class _MD:
        eligible = {reb[0]: frozenset({"A", "B"}), reb[2]: frozenset({"B", "C"})}
    v = R._EligibleView(_MD())
    check("exact match on a rebalance date", v.eligible(reb[2]) == {"B", "C"})
    # .asof, not exact: the signal grid asks about the last session in the panel, which is not usually
    # a rebalance date. An exact lookup returns nothing, and panel_signals then widens the grid to the
    # WHOLE universe - showing names the run was forbidden to buy, with no sign anything is wrong.
    check("mid-month falls back to the prior set, not empty",
          v.eligible(cal[30]) == {"A", "B"}, f"{v.eligible(cal[30])}")
    check("before the first set it does not raise", v.eligible(pd.Timestamp("2000-01-01")) == {"A", "B"})

    # ---- the equity frame -------------------------------------------------------------------------
    r = dict(nav=nav, nav_pretax=nav * 1.02, invested=nav * 0.9, bench=nav * 0.8)
    eq = R._equity(r)
    check("equity.csv carries cash and deployment, not just NAV",
          {"nav", "nav_pretax", "invested", "cash", "deployment_pct", "drawdown_pct",
           "benchmark"} <= set(eq.columns), f"{list(eq.columns)}")
    check("drawdown is never positive", float(eq["drawdown_pct"].max()) <= 1e-9)
    check("deployment stays in [0, 100]", eq["deployment_pct"].between(0, 100).all())

    # ---- the trade book, which must exist on the INTERNAL engine too ----------------------------
    class _Led:
        trades = [dict(date=cal[1], symbol="A", side="BUY", qty=10, px=100.0,
                       commission=1.5, slippage=0.5),
                  dict(date=cal[2], symbol="A", side="SELL", qty=-10, px=110.0,
                       commission=1.6, slippage=0.6)]
        cost_paid, tax_paid, interest = 4.2, 20.0, 1.0
    tb = R._tradebook(dict(ledger=_Led()))
    check("the trade book comes from the ledger, so --engine internal is auditable",
          len(tb) == 2 and "price" in tb.columns and "charges" in tb.columns, f"{list(tb.columns)}")
    check("notional is computed", float(tb["notional"].sum()) == 2100.0)
    bs = R._by_symbol(tb)
    check("and rolls up per symbol", list(bs.index) == ["A"] and int(bs.loc["A", "fills"]) == 2)
    check("a run with no fills gives an empty book, not a crash",
          R._tradebook(dict(ledger=None)).empty)

    fr = R._frictions(dict(ledger=_Led()))
    check("frictions report the authoritative total alongside the split",
          fr["total_frictions"] == fr["cost_paid"] + fr["tax"], f"{fr}")


def test_nautilus_cash_budget():
    """The cash budget that stops AccountBalanceNegative aborting a run.

    Two production aborts came from `_submit`, and neither was caught by a test because that method
    needs a running Nautilus engine. This checks the source for the properties the fixes establish,
    and checks the budget arithmetic independently.
    """
    print(chr(10) + "-- nautilus cash budget --")
    import re
    src = (Path(__file__).resolve().parent / "nautilus_run.py").read_text(encoding="utf-8")

    # ---- 1. the budget exists and starts from SETTLED cash --------------------------------------
    check("the budget starts from settled cash, not equity",
          "balance_free(INR)" in src and "budget = self.portfolio.account(VENUE).balance_free" in src)
    check("an unaffordable buy is refused, not submitted",
          'reason="unfunded"' in src)
    # The refusal must `continue` BEFORE submit_order, or the budget is decorative.
    i = src.find('reason="unfunded"')
    j = src.find("self.submit_order", i)
    k = src.find("continue", i)
    check("and the refusal short-circuits before submit_order", -1 < k < j,
          f"continue at {k}, submit at {j}")

    # ---- 2. BOTH allowances, applied in the ADVERSE direction on BOTH legs ----------------------
    # 2021-12-02: a single 3*COST_PCT = 0.45% cushion against a measured mean |gap| of 1.01%.
    check("there is a separate GAP allowance, not just a cost one",
          "NAUTILUS_GAP_ALLOW" in src and "_GAP" in src)
    check("buys are inflated by cost AND gap",
          "_ALLOW = 1.0 + _COST + _GAP" in src)
    check("sell proceeds are HAIRCUT by cost AND gap",
          "max(0.0, 1.0 - _COST - _GAP)" in src)
    # A sell priced at the close must never be assumed to raise the close.
    check("proceeds are not credited at the unadjusted close",
          not re.search(r"budget \+= abs\(delta\) \* px\s*$", src, re.M))

    # ---- 3. the arithmetic, independent of the engine ------------------------------------------
    cost, gap = 3.0 * 0.0015, 0.01
    allow = 1.0 + cost + gap

    def sim(cash, orders, px):
        """orders: list of signed deltas, sells first. Returns (submitted, refused, budget)."""
        budget, sent, refused = cash, [], []
        for d in sorted(orders):
            if d > 0:
                need = d * px * allow
                if need > budget:
                    refused.append(d)
                    continue
                budget -= need
                sent.append(d)
            else:
                sent.append(d)
                budget += abs(d) * px * max(0.0, 1.0 - cost - gap)
        return sent, refused, budget

    # a sell funds a buy, and the budget never goes negative
    sent, refused, bud = sim(0.0, [-100, +90], 100.0)
    check("a sell's proceeds fund a buy", +90 in sent and not refused, f"{sent} {refused}")
    check("and the budget stays non-negative", bud >= 0, f"{bud:,.2f}")

    # the same buy WITHOUT the sell is refused - this is the 2021-07-02 case
    sent, refused, bud = sim(0.0, [+90], 100.0)
    check("with no cash and no sell, the buy is refused (2021-07-02)",
          refused == [+90] and not sent, f"{sent} {refused}")

    # a buy that fits only if the gap is ignored must be refused - the 2021-12-02 case
    px, qty = 100.0, 100
    bare = qty * px                       # what the old cushion-free estimate would have allowed
    sent, refused, bud = sim(bare * 1.004, [+qty], px)   # enough for 0.4% of cushion, not 1.45%
    check("a buy that only fits without the gap allowance is refused (2021-12-02)",
          refused == [+qty], f"{sent} {refused}")
    sent, refused, bud = sim(bare * allow, [+qty], px)
    check("and the same buy IS sent when the cushion is really there", sent == [+qty] and not refused)

    # ---- 4. the knob is configured, and documented as measured ---------------------------------
    from qbeast_backtest import config as C
    g = float(getattr(C, "NAUTILUS_GAP_ALLOW", -1))
    check("config carries NAUTILUS_GAP_ALLOW", 0 < g < 0.10, f"{g}")
    cfg = (Path(C.__file__)).read_text(encoding="utf-8")
    check("and says the default was MEASURED, not chosen",
          "MEASURED" in cfg[cfg.find("NAUTILUS_GAP_ALLOW") - 1200:cfg.find("NAUTILUS_GAP_ALLOW")])

def main() -> int:
    print("=" * 90)
    print("Backtest_Modular - contract conformance")
    print("=" * 90)
    for t in (test_marketdata, test_strategyoutput, test_executionresult, test_tradability,
              test_nautilus_cash_budget,
              test_report_adapters,
              test_registry_and_shape):
        try:
            t()
        except Exception as e:                                   # noqa: BLE001
            import traceback
            FAIL.append(t.__name__)
            print(f"  [FAIL] {t.__name__} raised {type(e).__name__}: {e}")
            traceback.print_exc()
    print("\n" + "=" * 90)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"   FAILED: {f}")
    print("=" * 90)
    print("\nNOTE: this suite tests the BOUNDARIES. That the pipeline still reproduces Backtest_Core\n"
          "      to the rupee is proved by `python reconcile.py`, and the cost/tax arithmetic by\n"
          "      `python -m qbeast_exec.selftest`. All three are part of the acceptance criteria.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
