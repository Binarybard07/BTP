"""
qbeast_backtest/data_io.py
-----------------------
Clean data loader. Reads the existing source (config.DATA_DIR) read-only and returns validated price
series. Dates are parsed EXPLICITLY as DD-MM-YYYY (config.DATE_FORMAT) - never inferred - because
inference silently corrupts DD-MM vs MM-DD and produces plausible-looking but wrong numbers.

Public API:
    load_ohlcv(symbol)   -> validated OHLCV DataFrame indexed by date (FULL history)
    load_index()         -> the NIFTY-100 index series
    universe()           -> tradeable stock symbols (index excluded)
    trading_window(df)   -> slice to [BACKTEST_START, BACKTEST_END]
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from qbeast_backtest.config import BACKTEST_END, BACKTEST_START, DATA_DIR, DATE_FORMAT, INDEX_SYMBOL


class DataError(Exception):
    """Raised when a file cannot be parsed into a trustworthy price series."""


def load_ohlcv(symbol: str, directory=None) -> pd.DataFrame:
    """Load one symbol's full history, validated. Raises DataError rather than returning junk.

    `directory` overrides config.DATA_DIR — used to read a SIGNAL index (e.g. NIFTY 50) that lives
    outside the traded universe's folder."""
    path = (DATA_DIR if directory is None else Path(directory)) / f"{symbol}.csv"
    if not path.exists():
        raise DataError(f"{symbol}: file not found at {path}")
    df = pd.read_csv(path).rename(columns=lambda c: c.strip().lower())

    need = ["date", "open", "high", "low", "close"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise DataError(f"{symbol}: missing columns {missing}")
    if "volume" not in df.columns:
        df["volume"] = 0.0                                  # index files carry no volume

    dt = pd.to_datetime(df["date"].astype(str), format=DATE_FORMAT, errors="coerce")
    if dt.isna().any():
        raise DataError(f"{symbol}: {int(dt.isna().sum())} unparseable dates (expected {DATE_FORMAT})")

    df = df.assign(date=dt).sort_values("date").set_index("date")
    if df.index.duplicated().any():
        raise DataError(f"{symbol}: {int(df.index.duplicated().sum())} duplicate dates")

    out = df[["open", "high", "low", "close", "volume"]].astype(float)
    if not (out[["open", "high", "low", "close"]] > 0).all().all():
        raise DataError(f"{symbol}: non-positive prices present")

    # repair single-tick OHLC violations (re-derive high/low as the true extremes) rather than
    # discarding a long history
    viol = (out["high"] < out[["open", "close", "low"]].max(axis=1)) | \
           (out["low"] > out[["open", "close", "high"]].min(axis=1))
    if viol.any():
        ext = out[["open", "high", "low", "close"]]
        out.loc[viol, "high"] = ext.max(axis=1)[viol]
        out.loc[viol, "low"] = ext.min(axis=1)[viol]
    return out


def load_index() -> pd.DataFrame:
    """The BENCHMARK index — what returns are reported against, and the trading calendar.

    Read from the SHARED config.INDICES_DIR, not from the universe's own data folder. Every universe
    benchmarks against an index that already exists once, centrally; a per-universe copy is how a
    benchmark file ends up holding another index's prices, which has happened twice here and bought
    ~14pp of alpha that was not real. One file, one source.

    The universe folder is still tried as a FALLBACK so an older layout that kept its own copy keeps
    working — but it says so, because that copy is the thing worth distrusting."""
    from qbeast_backtest.config import INDICES_DIR
    try:
        return load_ohlcv(INDEX_SYMBOL, directory=INDICES_DIR)
    except DataError as shared_err:
        try:
            df = load_ohlcv(INDEX_SYMBOL)                      # legacy: a copy inside the universe folder
        except DataError:
            have = sorted(p.stem for p in Path(INDICES_DIR).glob("*.csv")) if Path(INDICES_DIR).exists() \
                else []
            raise DataError(
                f"benchmark {INDEX_SYMBOL!r} not found in the shared indices folder ({shared_err}), "
                f"nor in the universe folder {DATA_DIR}. INDICES_DIR={INDICES_DIR} holds: "
                f"{', '.join(have) if have else '(nothing)'}. Fix `index_symbol` for this universe in "
                f"universes.py so it matches one of those names exactly.") from shared_err
        print(f"  [index] WARNING: benchmark {INDEX_SYMBOL!r} read from the UNIVERSE folder "
              f"({DATA_DIR}), not the shared {INDICES_DIR}. A per-universe benchmark copy is the known "
              f"route to serving another index's prices as this one's -- verify it or move it.",
              flush=True)
        return df


def load_signal_index() -> pd.DataFrame:
    """The index the REGIME / TREND GATE runs on.

    Normally the same as the benchmark, but they are separable: you may want to trade the NIFTY-100
    universe while reading the regime off NIFTY 50 (broader, longer history, less noisy). Configure with
    config.SIGNAL_INDEX (+ SIGNAL_INDEX_DIR when the file lives outside the universe folder); when
    SIGNAL_INDEX is None this returns load_index() unchanged, so behaviour is identical by default."""
    from qbeast_backtest.config import SIGNAL_INDEX, SIGNAL_INDEX_DIR
    if not SIGNAL_INDEX or str(SIGNAL_INDEX).upper() == str(INDEX_SYMBOL).upper():
        return load_index()
    try:
        return load_ohlcv(SIGNAL_INDEX, directory=SIGNAL_INDEX_DIR)
    except DataError as e:
        raise DataError(
            f"SIGNAL_INDEX {SIGNAL_INDEX!r} could not be loaded ({e}). Set config.SIGNAL_INDEX_DIR to the "
            f"folder holding it, or set SIGNAL_INDEX=None to use the benchmark {INDEX_SYMBOL!r}.") from e


def universe() -> list[str]:
    """Tradeable stocks: every CSV except the index itself."""
    return sorted(p.stem for p in DATA_DIR.glob("*.csv") if p.stem.upper() != INDEX_SYMBOL.upper())


def trading_window(df: pd.DataFrame) -> pd.DataFrame:
    return df.loc[BACKTEST_START:BACKTEST_END]
