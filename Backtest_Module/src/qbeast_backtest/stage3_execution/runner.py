r"""
stage3_execution/runner.py
==========================
Turns a `StrategyOutput` into an `ExecutionResult`.

THE SIMULATION LOOP is the one from Backtest_Core/backtest.py, moved here with one change: it no
longer DECIDES anything. Where the monolith called `strategy.target_weights(...)` inside the loop,
this reads `out.targets[t]`. That is the whole difference, and it is what makes the strategy
swappable - the loop below cannot tell which strategy produced the dict it is executing.

THE T -> T+1 CONTRACT, unchanged and load-bearing:
    a target decided on session t is stored as `pending = (cal[i+1], weights)`
    and executed at cal[i+1]'s OPEN. No same-bar execution anywhere.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from qbeast_backtest.contracts import ExecutionResult, MarketData, StrategyOutput


def _internal(md: MarketData, out: StrategyOutput, *, tax: bool, bands, stair, slip):
    """The internal-ledger path. Identical mechanics to Backtest_Core, decisions read from `out`."""
    from qbeast_backtest import breaker
    from qbeast_backtest import config as C
    from qbeast_backtest.engine import Ledger

    led = Ledger(C.INITIAL_CAPITAL, tax=tax, bands=bands, staircase=stair)
    # A FRESH breaker per simulated book: it carries this book's own high-water mark, so sharing one
    # across the taxed and gross runs would leak the first one's peak into the second.
    brk = breaker.from_config()
    led.breaker = brk

    cal = md.calendar
    closes, opens = md.closes, md.opens
    sig = md.signal_index.reindex(cal).ffill()
    # Same series the Nautilus path uses, and for the same reason: the breaker reads it with .asof().
    trend = (out.execution_inputs or {}).get("trend_up")
    if trend is None and out.diagnostics is not None and "trend_up" in out.diagnostics:
        trend = out.diagnostics["trend_up"]

    pending = None
    for i, t in enumerate(cal):
        if pending is not None and pending[0] == t:
            led.execute(t, pending[1], opens.loc[t], slip)
            pending = None
        nav = led.mark(t, closes.loc[t])
        if brk.enabled:
            # Stepped AFTER the mark, so the drawdown is measured on the NAV the reports show.
            up = bool(trend.asof(t)) if trend is not None else True
            if brk.step(t, nav, sig.get(t, np.nan), up) == "HALT":
                if i + 1 < len(cal):
                    pending = (cal[i + 1], {})          # flatten the whole book at the next open
                continue                                # a halt session never also rebalances
            if brk.halted:
                continue                                # stay in cash until the re-entry rule fires
        # THE ONLY LINE THAT USED TO BE A DECISION. `t in out.targets` is the strategy's choice of
        # when to act; an empty dict is a flatten instruction and is NOT the same as no key.
        if t in out.targets and i + 1 < len(cal):
            pending = (cal[i + 1], dict(out.targets[t]))
    return led


def _nautilus(md: MarketData, out: StrategyOutput, *, bands, stair, slip):
    """The NautilusTrader path - the source of record. Fills land on the next open; the fills are then
    replayed through the shared tax + cash-yield overlay, which Nautilus has no concept of."""
    from qbeast_backtest import config as C
    from qbeast_backtest import nautilus_run as NR
    from qbeast_backtest.nautilus_backtest import RegimeFeeModel, build_engine, index_instrument_symbol, make_equity
    from qbeast_backtest.engine import liquidity_slippage

    cal = md.calendar
    syms = sorted({s for w in out.targets.values() for s in w})
    if not syms:
        raise RuntimeError("the strategy never targeted a single name - nothing to execute on Nautilus")

    # Nautilus is driven by the module-level handoff NR already uses. Targets come from the STRATEGY
    # OUTPUT rather than being recomputed, which is the point of the split.
    NR._TARGETS = {pd.Timestamp(t): dict(w) for t, w in out.targets.items()}
    NR._REBAL_DATES = {pd.Timestamp(t).date() for t in out.targets}
    NR._FILLS, NR._ORDER_DATE = [], {}
    NR._INITIAL = float(C.INITIAL_CAPITAL)
    NR._BANDS, NR._STAIR = bands, stair           # the same tradability backstops as the internal path

    # THE BREAKER'S RE-ENTRY INPUTS. `nautilus_run` refuses to start when DD_EXIT is armed and these
    # are missing - "rather than trading a breaker that can halt but never resume" - and it is right
    # to: a halt with no re-entry rule is a book that goes to cash once and stays there for the rest of
    # the run, which looks like a strategy decision rather than a wiring fault.
    #
    # The monolith populated them inside `precompute()`, which also recomputed the targets. This path
    # does not call precompute (the targets come from stage 2, which is the point of the split), so the
    # two series are handed over explicitly. Both are already in the StrategyOutput: the signal index
    # is stage 1's, and the trend gate is a stage-2 diagnostic.
    NR._SIG_CLOSE = md.signal_index
    # From execution_inputs, which is RAW. Falling back to the reindexed diagnostic would silently
    # change the breaker's re-entry decisions - see the note on StrategyOutput.execution_inputs.
    NR._TREND_UP = (out.execution_inputs or {}).get("trend_up")
    if NR._TREND_UP is None and out.diagnostics is not None and "trend_up" in out.diagnostics:
        print("  ** stage3: the strategy published no RAW trend_up in execution_inputs; falling back "
              "to the reindexed diagnostic. The breaker's re-entry will differ from a full-resolution "
              "run. **", flush=True)
        NR._TREND_UP = out.diagnostics["trend_up"]
    if getattr(C, "DD_EXIT", None) and NR._TREND_UP is None:
        raise RuntimeError(
            "config.DD_EXIT is armed but this strategy published no 'trend_up' diagnostic, so the "
            "breaker would have no re-entry rule. Either publish one in StrategyOutput.diagnostics, "
            "or run with DD_EXIT = None.")

    # RAW CLOSES, NOT FORWARD-FILLED. `md.closes` is the ffilled DECISION frame - correct for ranking
    # and sizing, wrong for feeding an exchange. A forward-fill invents a bar on every day a stock did
    # NOT trade, repeating the stale price, and Nautilus then happily fills against it.
    #
    # MEASURED: feeding the ffilled frame put the Nautilus arm 2.44% above the monolith at the end
    # (max 4.99% intra-run, 2,497 of 2,498 sessions differing) while the internal arm was exact to the
    # rupee - because the internal ledger marks on the decision frame by design and never sees a bar.
    # `make_bars` skips a non-finite close, so the raw frame correctly produces NO bar on a no-trade
    # day, which is what the monolith feeds.
    o = md.opens.reindex(cal)
    c = md.history_closes.reindex(cal)
    v = md.volumes.reindex(cal)
    idxc = md.benchmark.reindex(cal)

    # FULL-HISTORY frames for the fee model, matching the monolith. `c`/`v` below are the WINDOWED
    # bars Nautilus trades on; they must not also define what a trade costs.
    #
    # This is THE line that broke the Nautilus reconciliation. `RegimeFeeModel` wraps the result in
    # `NautilusFeeModel(cfg, extra_rate=dict(slippage))` - taking the FLAT DICT VALUES and discarding
    # the size-aware model - whereas the internal ledger calls `.rate(sym, date, qty, price)` and uses
    # the model, never the dict. So windowing the frames changed the per-symbol rate on 217 of 217
    # symbols (mean 9.4 bps) and moved the Nautilus NAV +5.09% over ten years, while the internal arm
    # stayed exact to the rupee. One green arm proved nothing about the other.
    engine = build_engine(int(C.INITIAL_CAPITAL),
                          fee_model=RegimeFeeModel(
                              None, liquidity_slippage(md.history_closes, md.history_volumes)))
    idx = make_equity(index_instrument_symbol())
    NR._INDEX_ID = idx.id
    engine.add_instrument(idx)
    engine.add_data(NR.make_bars(idx, idxc, idxc, idxc, idxc, None, offset_ns=0))
    for s in syms:
        if s not in c.columns:
            continue
        inst = make_equity(s)
        engine.add_instrument(inst)
        engine.add_data(NR.make_bars(inst, o[s], None, None, c[s],
                                     v[s] if s in v.columns else None, offset_ns=1))
    from nautilus_trader.trading.strategy import StrategyConfig
    engine.add_strategy(NR.RegimeExec(config=StrategyConfig()))
    engine.run()
    from qbeast_backtest.nautilus_books import export_books
    export_books(engine, "backtest", C.OUT_DIR, order_dates=NR._ORDER_DATE, opens=o)
    fills = list(NR._FILLS)
    engine.dispose()

    led = NR.replay_fills(fills, c, cal, C.INITIAL_CAPITAL, opens=o)
    led_pre = NR.replay_fills_pretax(fills, c, cal, C.INITIAL_CAPITAL, opens=o)
    return led, led_pre, fills


def execute(md: MarketData, out: StrategyOutput, *, engine: str | None = None,
            bands=None, stair=None, verbose: bool = True) -> ExecutionResult:
    """Run `out`'s decisions through costs, tax and an exchange."""
    from qbeast_backtest import config as C
    from qbeast_backtest.engine import liquidity_slippage

    eng = str(engine or C.EXEC_ENGINE).lower()

    # THE SLIPPAGE MODEL IS BUILT FROM FULL HISTORY, not the trading window. Two reasons, and the
    # second one is a bug this got wrong:
    #
    #  1. On its own merits: a liquidity estimate should use every bar available, not only the window
    #     being tested. Windowing it makes the same symbol cost a different amount depending on which
    #     slice you happen to be backtesting.
    #  2. THE TWO ENGINES CONSUME IT DIFFERENTLY, so windowing broke one of them and not the other:
    #       internal ledger  calls .rate(sym, date, qty, price) -> in dynamic mode that evaluates the
    #                        size-aware MODEL, and the dict values are never read
    #       Nautilus         gets NautilusFeeModel(cfg, extra_rate=dict(slippage)) -> that takes the
    #                        FLAT DICT VALUES and discards the model
    #     MEASURED: windowing changed the per-symbol rate on 217 of 217 symbols, mean 9.4 bps. The
    #     internal arm stayed exact to the rupee (rates unused) while the Nautilus arm drifted +5.09%
    #     over ten years - which is exactly the shape of "a green test suite proves one path only".
    slip = liquidity_slippage(md.history_closes, md.history_volumes)
    fills_df = None

    if eng == "nautilus":
        led, led_pre, fills = _nautilus(md, out, bands=bands, stair=stair, slip=slip)
        fills_df = pd.DataFrame(fills) if fills else None
    else:
        led = _internal(md, out, tax=True, bands=bands, stair=stair, slip=slip)
        led_pre = _internal(md, out, tax=False, bands=bands, stair=stair, slip=slip)

    nav = pd.Series(led.nav_hist).sort_index()
    nav_pre = pd.Series(led_pre.nav_hist).sort_index()
    invested = pd.Series(led.invested_hist).sort_index()
    bench = md.benchmark.reindex(nav.index).ffill().bfill()
    bench = bench * C.INITIAL_CAPITAL / bench.iloc[0]

    refused = None
    if stair is not None:
        try:
            refused = stair.blocked_table()
        except Exception:                                      # noqa: BLE001
            refused = None

    res = ExecutionResult(nav=nav, nav_pretax=nav_pre, invested=invested, benchmark=bench,
                          ledger=led, engine=eng, fills=fills_df, refused=refused,
                          notes=(f"engine={eng}",))
    if verbose:
        n_ref = 0 if refused is None or refused.empty else int((refused["side"] == "BUY").sum())
        # In a correct run this is ZERO: stage 1 already removed the flagged names from the pool, so
        # nothing flagged should ever reach the ledger. A non-zero count means something bypassed the
        # gate, which is worth hearing about rather than treating as the gate "working".
        # UNDER v2.4.1 A NON-ZERO COUNT IS NORMAL, and the old message said the opposite.
        # Stage 1 removes flagged names from the CANDIDATE POOL at rebalance dates. The v2.4.1 gate
        # also refuses sells and exits, on every session - so a name that gets tagged while HELD is
        # refused at the ledger between rebalances, which is the rule working rather than a bypass.
        n_all = 0 if refused is None or refused.empty else int(len(refused))
        print(f"  [stage3] engine {eng} | staircase/circuit backstop refused {n_all} order(s) at the "
              f"ledger ({n_ref} buy, {n_all - n_ref} sell/exit)"
              + ("" if n_all else "  (nothing reached it - the pool filter had already removed them)"),
              flush=True)
        if n_all - n_ref:
            print(f"      of those, {n_all - n_ref} were SELLS or EXITS on a held position: under "
                  f"v2.4.1 a tagged name cannot be sold into a locked market. The position is still "
                  f"marked to market, so the cost of being trapped is in the NAV.", flush=True)
        for p in res.validate():
            print(f"  ** stage3 EXECUTION PROBLEM: {p} **", flush=True)
    return res
