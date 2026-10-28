r"""
stage1_data/loader.py
=====================
DATA INSERTION. Loads the universe's OHLCV, resolves the benchmark and the signal index, fixes the
trading calendar, applies survivorship and the staircase gate, and returns ONE validated
`contracts.MarketData`.

It does not re-implement any loading or detection logic. `data_io`, `engine._ohlcv_frames`,
`membership.Membership` and `qbeast_exec.StaircaseFlag` are the certified code and are called as-is;
what is new is that their results are assembled into a single object with a PER-SESSION ELIGIBLE SET,
instead of six loose frames each caller re-derives its own view from.

The three small helpers below (`load_frames`, `signal_index_close`, `rebalance_dates`) were previously
inside the monolithic `backtest.py`. They are data and calendar concerns, so they live here now -
moved verbatim, not rewritten.
"""
from __future__ import annotations

import pandas as pd

from qbeast_backtest.contracts import MarketData

from .tradability import TradabilityGate

# The gate object is NOT part of the contract - stage 2 must never see it - but the runner needs its
# report to write the audit CSVs. One slot, set here, read by the pipeline.
LAST_GATE: list = [None]


# ===================================================================================================
# frames, benchmark, signal index, calendar   (moved verbatim from Backtest_Core/backtest.py)
# ===================================================================================================
def load_frames():
    """FULL-history OPEN/CLOSE/VOLUME wide frames + index close, from the clean source."""
    from qbeast_backtest import data_io
    idx = data_io.load_index()["close"]
    opens, closes, vols = {}, {}, {}
    for sym in data_io.universe():
        try:
            df = data_io.load_ohlcv(sym)
        except data_io.DataError:
            continue
        if df.empty:
            continue
        u = sym.upper()
        opens[u], closes[u], vols[u] = df["open"], df["close"], df["volume"]
    return pd.DataFrame(opens), pd.DataFrame(closes), pd.DataFrame(vols), idx


def signal_index_close(fallback=None) -> pd.Series:
    """The close series the REGIME / TREND GATE runs on (config.SIGNAL_INDEX).

    Returns the BENCHMARK unchanged when SIGNAL_INDEX is unset, so the default path is unchanged. A
    different signal index is announced loudly, because silently gating on the wrong index is the class
    of bug that once wrote MidCap prices into the NIFTY-100 file."""
    from qbeast_backtest import data_io
    from qbeast_backtest.config import INDEX_SYMBOL, SIGNAL_INDEX
    if not SIGNAL_INDEX or str(SIGNAL_INDEX).upper() == str(INDEX_SYMBOL).upper():
        return data_io.load_index()["close"] if fallback is None else fallback
    s = data_io.load_signal_index()["close"]
    print(f"  REGIME/TREND GATE reads {SIGNAL_INDEX!r} "
          f"({s.index[0].date()}..{s.index[-1].date()}, {len(s):,} bars); "
          f"BENCHMARK stays {INDEX_SYMBOL!r}", flush=True)
    return s


def rebalance_dates(cal: pd.DatetimeIndex) -> set:
    """First trading day of each period, per config.REBALANCE_FREQ (monthly | weekly | quarterly)."""
    from qbeast_backtest.config import REBALANCE_FREQ
    s = pd.Series(cal, index=cal)
    if REBALANCE_FREQ == "weekly":
        iso = cal.isocalendar()
        keys = [iso["year"].to_numpy(), iso["week"].to_numpy()]
    elif REBALANCE_FREQ == "quarterly":
        keys = [cal.year, cal.quarter]
    else:                                                 # monthly (default)
        keys = [cal.year, cal.month]
    return set(s.groupby(keys).last())


# ===================================================================================================
# the gates
# ===================================================================================================
def _gate_frames() -> dict:
    """{symbol: OHLCV} for the band and staircase gates.

    Uses `engine._ohlcv_frames`, the same builder the slippage model reads, so all three gates see
    identical bars. They need HIGH and LOW - the pattern is a close sitting AT the band, and the band
    is inferred from the bar - which `load_frames()` drops, hence the separate pass."""
    from qbeast_backtest import data_io
    from qbeast_backtest.engine import _ohlcv_frames
    syms = tuple(sorted(str(s).upper() for s in data_io.universe()))
    return _ohlcv_frames(syms)


def _staircase(frames: dict):
    """The staircase gate, or None. A gate that silently passes everything is worse than an absent
    one, so a failure to build it is announced rather than swallowed."""
    from qbeast_backtest import config as C
    if not getattr(C, "USE_STAIRCASE_FLAG", False):
        return None
    if not frames:
        print("  [staircase] no OHLCV frames could be built - gate is OFF for this run", flush=True)
        return None
    # `_STAIRCASE_CLS` lets a comparison harness substitute the PRE-2.4.1 class (StaircaseFlagV1)
    # without touching this loader - see staircase_ab.py, which runs the old and new rules on the
    # same window so the difference between them is measured rather than argued about. Normal runs
    # leave it unset and get the v2.4.1 gate.
    import qbeast_exec as Q
    cls = globals().get("_STAIRCASE_CLS") or Q.StaircaseFlag
    return cls(frames, C.staircase_config())


def _price_bands(frames: dict):
    """The Tier-1 circuit-lock gate (config.USE_PRICE_BANDS), or None."""
    from qbeast_backtest import config as C
    if not getattr(C, "USE_PRICE_BANDS", False):
        return None
    try:
        from qbeast_exec import BandConfig, PriceBandModel
    except Exception as e:                                    # noqa: BLE001
        print(f"  [bands] qbeast_exec unavailable ({e}); price-band gate OFF", flush=True)
        return None
    usable = {s: d for s, d in frames.items()
              if d is not None and not d.empty and {"high", "low", "close"}.issubset(d.columns)}
    m = PriceBandModel(usable, BandConfig())
    banded = sum(1 for s in usable if m.band(s) is not None)
    print(f"  [bands] price-band gate ON: {banded}/{len(usable)} symbols with an inferred band",
          flush=True)
    return m


# ===================================================================================================
# the one entry point
# ===================================================================================================
def load_market_data(*, verbose: bool = True) -> MarketData:
    """Build the single object stage 2 is allowed to read."""
    from qbeast_backtest import config as C
    from qbeast_backtest.membership import Membership

    opens, closes, volumes, index_close = load_frames()
    sig_close = signal_index_close(index_close)

    cal = index_close.index[(index_close.index >= C.BACKTEST_START) &
                            (index_close.index <= C.BACKTEST_END)]
    if not len(cal):
        raise RuntimeError(f"no benchmark sessions between {C.BACKTEST_START.date()} and "
                           f"{C.BACKTEST_END.date()} - check BACKTEST_START / the index file")
    closes_ff = closes.reindex(cal).ffill()
    rebals = sorted(d for d in rebalance_dates(cal))

    frames = _gate_frames()
    stair = _staircase(frames)
    bands = _price_bands(frames)
    memb = Membership(available={str(s).upper() for s in closes.columns}) if C.POINT_IN_TIME else None

    gate = TradabilityGate(closes, frames, membership=memb, staircase=stair,
                           exclude_flagged=bool(getattr(C, "STAIRCASE_EXCLUDE_FROM_POOL", True)))
    # Built for the REBALANCE sessions only - the only days a strategy asks about, and the staircase
    # test is the one genuinely expensive thing in stage 1.
    eligible = gate.build(cal, closes_ff, only=rebals)
    gate.bands = bands                      # carried for the pipeline, not for the strategy
    LAST_GATE[0] = gate

    md = MarketData(
        calendar=cal, opens=opens.reindex(cal), closes=closes_ff, volumes=volumes.reindex(cal),
        # FULL history, for trailing-signal warm-up. The window above is for decisions and sizing.
        history_closes=closes, history_volumes=volumes,
        warmup_bars=int(getattr(C, "WARMUP_BARS", 400)),
        benchmark=index_close.reindex(cal).ffill(),
        signal_index=sig_close,                      # NOT reindexed: the gate reads it with .asof()
        eligible=eligible, rebalance_dates=rebals,
        universe=str(C.UNIVERSE), benchmark_name=str(C.INDEX_SYMBOL),
        signal_index_name=str(getattr(C, "SIGNAL_INDEX", None) or C.INDEX_SYMBOL),
        survivorship=bool(memb is not None),
        staircase_flagged={k: v for k, v in gate.report.flagged.items() if v},
        notes=(gate.report.summary(),))

    if verbose:
        print(f"  [stage1] {closes.shape[1]} symbols | calendar {cal[0].date()}..{cal[-1].date()} "
              f"({len(cal)} sessions, {len(rebals)} rebalances)", flush=True)
        print(f"  [stage1] survivorship {'ON (point-in-time)' if memb is not None else 'OFF'}", flush=True)
        print(f"  [stage1] {gate.report.summary()}", flush=True)
        for p in md.validate():
            print(f"  ** stage1 DATA PROBLEM: {p} **", flush=True)
    return md
