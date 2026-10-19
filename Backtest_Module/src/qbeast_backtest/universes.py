r"""
qbeast_backtest/universes.py
-------------------------
NAMED UNIVERSE PRESETS — switch the whole system between NIFTY 100 / MidCap 150 / SmallCap 250 / anything
else by changing ONE word in config.py.

WHY THIS EXISTS: a universe is not one setting, it is FOUR that must move together —

    SOURCE / DATA_DIR   where the per-symbol OHLCV CSVs live
    INDEX_SYMBOL        the benchmark, which must match the index CSV's FILENAME *inside that folder*
                        (they differ: "NIFTY 100.csv", "NIFTY100.csv", "NIFTYMIDCAP150.csv", ...)
    MEMBERSHIP_FILE     point-in-time constituents, if you have them for that index
    the Upstox index key used by refresh.py

Changing them by hand is how a real bug happened: INDEX_SYMBOL was moved to "NIFTY 100" while refresh.py
still fetched MIDCAP 150, so MidCap prices were written into the file named "NIFTY 100" — silently
corrupting both the benchmark AND the trend gate (which runs on the index). Presets make that impossible:
one name sets all of them, and `validate()` refuses a preset whose pieces do not line up.

USE
    # in config.py
    UNIVERSE = "nifty100"          # <- the ONLY line you change

    python universes.py            # list every preset and validate it against the disk
    python universes.py midcap150  # inspect one

PER-UNIVERSE SETTINGS: a preset may carry an `overrides` dict (see OVERRIDABLE below) for any config.py
setting that should differ by universe - top_n, costs, slippage, the execution engine.
Absent keys fall back to config.py's module-level default, so a preset records only what DIFFERS.

ADD YOUR OWN: copy a block into PRESETS below. `index_symbol` MUST equal the index CSV's stem in `source`.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["PRESETS", "OVERRIDABLE", "DATA_ROOT", "get", "names", "overrides", "validate",
           "describe"]

# ===================================================================================================
# WHERE THE DATA LIVES - the one setting a new machine has to change
# ===================================================================================================
# Every preset below is `DATA_ROOT / "<folder>"`. Point QBEAST_DATA_ROOT at your copy of the shared
# EoD store and every universe follows:
#
#     set QBEAST_DATA_ROOT=D:\market_data          (Windows)
#     export QBEAST_DATA_ROOT=/mnt/market_data     (POSIX)
#
# The default is the machine this was built on, so nothing changes for an existing checkout. The
# SUBFOLDER names stay literal on purpose: they are the data's own layout, and `index_symbol` has to
# match a CSV filename inside one of them - which is exactly what `validate()` checks. Deriving them
# from a convention would hide a mismatched preset instead of refusing it.
DATA_ROOT = Path(os.environ.get("QBEAST_DATA_ROOT") or r"E:\Final_Data_Shared_22SEP2026")

# ---------------------------------------------------------------------------------------------------
# index_symbol MUST match the index CSV filename (without .csv) inside `source`.
# membership=None  -> USE_MEMBERSHIP=False: the universe is EVERY *.csv in the folder except the index.
# ---------------------------------------------------------------------------------------------------
PRESETS: dict[str, dict] = {
    "nifty100": dict(
        label="NIFTY 100 (Final_Data_Shared_22SEP2026 survivorship-clean EoD)",
        source=DATA_ROOT / r"Final_NIFTY100_EoD_Data",
        index_symbol="NIFTY 100",
        membership=None,
        note="Large caps. The most liquid set; lowest slippage and highest capacity.",
    ),
    "nifty100_ddq": dict(
        label="NIFTY 100 (clean set, point-in-time membership)",
        source=DATA_ROOT / r"Final_Survivorship_NIFTY100_EoD_Data",
        index_symbol="NIFTY 100",
        membership=DATA_ROOT / r"Survivorship_Bias\Nifty100_membership.csv",
        note="Large caps. Lowest slippage and highest capacity; least dispersion to pick from.",
    ),
    "midcap150": dict(
        label="NIFTY MidCap 150",
        source=DATA_ROOT / r"Final_NIFTYMidCap150_EoD_Data",
        index_symbol="NIFTY MIDCAP 150",
        membership=None,
        note="Mid caps. Wider spreads and ~2x the slippage of large caps; capacity drops sharply.",
    ),
    "midcap150_ddq": dict(
        label="NIFTY MidCap 150 (DDQ, 245 symbols incl. delisted)",
        source=DATA_ROOT / r"Final_Survivorship_NIFTYMidCap150_EoD_Data",
        index_symbol="NIFTY MIDCAP 150",
        membership=DATA_ROOT / r"Survivorship_Bias\Midcap150_membership.csv",
        note="Includes delisted names + a membership ledger -> the survivorship-honest small-cap test.",
    ),
    "midcap100": dict(
        label="NIFTY MidCap 100",
        source=DATA_ROOT / r"Final_NIFTYMidCap100_EoD_Data",
        index_symbol="NIFTY MIDCAP 100",
        membership=None,
        note="Mid caps. Wider spreads and ~2x the slippage of large caps; capacity drops sharply.",
    ),
    "midcap100_ddq": dict(
        label="NIFTY MidCap 100 (DDQ, 161 symbols incl. delisted)",
        source=DATA_ROOT / r"Final_Survivorship_NIFTYMidCap100_EoD_Data",
        index_symbol="NIFTY MIDCAP 100",
        membership=DATA_ROOT / r"Survivorship_Bias\Midcap100_membership.csv",
        note="Includes delisted names + a membership ledger -> the survivorship-honest small-cap test.",
    ),
    "midcap50": dict(
        label="NIFTY MidCap 50",
        source=DATA_ROOT / r"Final_NIFTYMidCap50_EoD_Data",
        index_symbol="NIFTY MIDCAP 50",
        membership=None,
        note="Mid caps. Wider spreads and ~2x the slippage of large caps; capacity drops sharply.",
    ),
    "midcap50_ddq": dict(
        label="NIFTY MidCap 50 (DDQ, 100 symbols incl. delisted)",
        source=DATA_ROOT / r"Final_Survivorship_NIFTYMidCap50_EoD_Data",
        index_symbol="NIFTY MIDCAP 50",
        membership=DATA_ROOT / r"Survivorship_Bias\Midcap50_membership.csv",
        note="Includes delisted names + a membership ledger -> the survivorship-honest small-cap test.",
    ),
    "smallcap250": dict(
        label="NIFTY SmallCap 250",
        source=DATA_ROOT / r"Final_NIFTYSmallCap250_EoD_Data",
        index_symbol="NIFTY SMLCAP 250",
        membership=None,
        note="Small caps. Highest momentum dispersion BUT the worst slippage/capacity - read the "
             "capacity table before believing any backtest here.",
    ),
    "smallcap250_ddq": dict(
        label="NIFTY SmallCap 250 (DDQ, 488 symbols incl. delisted)",
        source=DATA_ROOT / r"Final_Survivorship_NIFTYSmallCap250_EoD_Data",
        index_symbol="NIFTY SMLCAP 250",
        membership=DATA_ROOT / r"Survivorship_Bias\SmallCap250_membership.csv",
        note="Includes delisted names + a membership ledger -> the survivorship-honest small-cap test.",
    ),
"nifty500": dict(
        label="NIFTY 500",
        source=DATA_ROOT / r"Final_NIFTY500_EoD_Data",
        index_symbol="NIFTY 500",
        membership=None,
        note="Small caps. Highest momentum dispersion BUT the worst slippage/capacity - read the "
             "capacity table before believing any backtest here.",
    ),
    "nifty500_ddq": dict(
        label="NIFTY 500 (DDQ, 633 symbols incl. delisted)",
        source=DATA_ROOT / r"Final_Survivorship_NIFTY500_EoD_Data",
        index_symbol="NIFTY 500",
        membership=DATA_ROOT / r"Survivorship_Bias\nifty500_membership.csv",
        note="Includes delisted names + a membership ledger -> the survivorship-honest small-cap test.",
    ),
"nifty50": dict(
        label="NIFTY 50",
        source=DATA_ROOT / r"Final_NIFTY50_EoD_Data",
        index_symbol="Nifty 50",
        membership=None,
        note="Small caps. Highest momentum dispersion BUT the worst slippage/capacity - read the "
             "capacity table before believing any backtest here.",
    ),
    "nifty50_ddq": dict(
        label="NIFTY 50 (DDQ, 68 symbols incl. delisted)",
        source=DATA_ROOT / r"Final_Survivorship_NIFTY50_EoD_Data",
        index_symbol="Nifty 50",
        membership=DATA_ROOT / r"Survivorship_Bias\nifty50_membership.csv",
        note="Includes delisted names + a membership ledger -> the survivorship-honest small-cap test.",
    ),
    "nifty200": dict(
        label="NIFTY 200",
        source=DATA_ROOT / r"Final_NIFTY200_EoD_Data",
        index_symbol="Nifty 200",
        membership=None,
    ),
    "nifty200_ddq": dict(
        label="NIFTY 200 (DDQ, 258 symbols incl. delisted)",
        source=DATA_ROOT / r"Final_Survivorship_NIFTY200_EoD_Data",
        index_symbol="Nifty 200",
        membership=DATA_ROOT / r"Survivorship_Bias\nifty200_membership.csv",
        note="Includes delisted names + a membership ledger -> the survivorship-honest small-cap test.",
    ),
    "microcap250": dict(
        label="NIFTY MicroCap 250",
        source=DATA_ROOT / r"Final_NIFTYMicroCap250_EoD_Data",
        index_symbol="NIFTY MICROCAP250",
        membership=None,
    ),
    "microcap250_ddq": dict(
        label="NIFTY 250 (DDQ, 304 symbols incl. delisted)",
        source=DATA_ROOT / r"Final_Survivorship_NiftyMicroCap250_EoD_Data",
        index_symbol="NIFTY MICROCAP250",
        membership=DATA_ROOT / r"Survivorship_Bias\microcap250_membership.csv",
        note="Includes delisted names + a membership ledger -> the survivorship-honest small-cap test.",
    ),
    "large_cap": dict(
        label="NIFTY Symbol Market Cap >20,000 cr",
        source=DATA_ROOT / r"Final_Large_Cap_EoD_Data",
        index_symbol="Nifty 100",
        membership=None,
    ),
    "midcap_upper": dict(
        label="NIFTY Symbol Market Cap from 10,000 to 20,000 cr",
        source=DATA_ROOT / r"Final_Mid_Cap_Upper_EoD_Data",
        index_symbol="NIFTY MIDCAP 150",
        membership=None,
    ),
    "midcap_lower": dict(
        label="NIFTY Symbol Market Cap from 5000 to 10,000 cr",
        source=DATA_ROOT / r"Final_Mid_Cap_Lower_EoD_Data",
        index_symbol="NIFTY MIDCAP 150",
        membership=None,
    ),
    "smallcap_upper": dict(
        label="NIFTY Symbol Market Cap frm 1500 to 5000 cr",
        source=DATA_ROOT / r"Final_Small_Cap_Upper_EoD_Data",
        index_symbol="NIFTY SMLCAP 250",
        membership=None,
    ),
    "smallcap_lower": dict(
        label="NIFTY Symbol Market Cap frm 500 to 1500 cr",
        source=DATA_ROOT / r"Final_Small_Cap_Lower_EoD_Data",
        index_symbol="NIFTY SMLCAP 250",
        membership=None,
    ),
    "final_1200_eod_data": dict(
        label="Market cap above Rs 1,500 cr - the full 1,272-symbol set (NOT an NSE index)",
        source=DATA_ROOT / r"Final_1200_EoD_Data",
        # index_symbol CANNOT be None. The benchmark CSV is not merely a comparison series: it is the
        # book's TRADING CALENDAR (prices.sync anchors on it), the series the regime/trend gate reads
        # whenever signal_index is None, and the measurement series in every report. None leaves the
        # paper calendar empty and the gate with nothing to read.
        # This set spans >1,500 cr and NSE publishes no list for it, so it borrows the broadest
        # benchmark in the index library - exactly as large_cap borrows Nifty 100 and smallcap_upper
        # borrows SMLCAP 250. NIFTY 500 is also the signal index that won 8/8 vintages.
        index_symbol="Final_all_INDICES",
        membership=None,
        note="A market-cap band, not an index. FOLDER-GATED: the folder IS the symbol list, so it "
             "must be re-exported before FOLDER_MAX_STALE_DAYS or the book refuses to trade.",
    ),
}

DEFAULT = "nifty100"


def names() -> list[str]:
    return sorted(PRESETS)


def get(name: str) -> dict:
    """The preset dict for `name`. Raises with the valid options rather than silently falling back."""
    key = str(name).strip().lower()
    if key not in PRESETS:
        raise KeyError(f"unknown UNIVERSE {name!r}. Available: {', '.join(names())}. "
                       f"Add one in universes.py:PRESETS.")
    return PRESETS[key]


# Every config.py setting listed here can be overridden PER UNIVERSE, via an `overrides` dict in the
# preset. That matters because the right value genuinely differs by universe: a SmallCap book should not
# be costed at large-cap slippage, and its capacity is an order of magnitude smaller. Anything absent
# from a preset's `overrides` falls back to the module-level default in config.py, so a preset stays
# short and records only what actually DIFFERS.
OVERRIDABLE: dict[str, str] = {
    "top_n": "names held",
    "initial_capital": "opening capital for the backtest / paper run",
    "cost_pct": "blended transaction charge per side (used when charges_mode='blended')",
    "charges_mode": "'blended' (one COST_PCT rate) | 'itemised' (qbeast_exec STT/exchange/SEBI/stamp/GST)",
    "slippage_mode": "'rank' (liquidity-rank interpolation) | 'dynamic' (Corwin-Schultz + sqrt impact) "
                     "| 'fixed' | 'none'",
    "slip_min": "rank mode: the MOST liquid stock's slippage",
    "slip_max": "rank mode: the LEAST liquid stock's slippage",
    "exec_engine": "'internal' (engine.Ledger) | 'nautilus' (NautilusTrader ledger/orders/books)",
    "signal_index": "index the REGIME/TREND GATE reads (None = the universe's own benchmark). "
                    "e.g. 'Nifty 50' to trade NIFTY-100 names but gate on NIFTY 50",
    "signal_index_dir": "folder holding signal_index.csv (default config.INDICES_DIR)",
    # per-universe strategy overrides are whatever YOUR strategy reads
    "rebalance_freq": "monthly | weekly | quarterly",
    "use_price_bands": "research backtest: circuit-lock tradability gate on/off",
    "use_price_bands_live": "live/paper path: circuit-lock tradability gate on/off",
    "cash_yield": "net-of-tax yield on idle cash",
}


def overrides(name: str) -> dict:
    """This universe's config overrides, validated. An unknown key is a typo that would otherwise sit
    in the preset doing nothing — exactly the failure mode `top_n` had before it was wired up."""
    ov = dict(get(name).get("overrides") or {})
    bad = sorted(set(ov) - set(OVERRIDABLE))
    if bad:
        raise KeyError(f"universe {name!r}: unknown override(s) {bad}. "
                       f"Valid keys: {', '.join(sorted(OVERRIDABLE))}")
    return ov


def validate(name: str) -> list[str]:
    """Check a preset against the DISK. Returns a list of problems ([] = healthy).

    Catches the failure modes that silently produce wrong numbers: a missing folder, an index_symbol that
    does not match any CSV in it (so the benchmark loads empty/wrong), a missing membership file, and a
    benchmark whose name cannot be mapped to an Upstox instrument key for the live feed."""
    p = get(name)
    probs: list[str] = []
    src = Path(p["source"])
    if not src.exists():
        return [f"source folder does not exist: {src}"]
    csvs = list(src.glob("*.csv"))
    if not csvs:
        probs.append(f"no CSV files in {src}")
    idx = src / f"{p['index_symbol']}.csv"
    if not idx.exists():
        near = [c.stem for c in csvs if any(t in c.stem.upper() for t in ("NIFTY", "MIDCAP", "SMLCAP"))]
        probs.append(f"index_symbol {p['index_symbol']!r} has no file {idx.name!r} in the source folder"
                     + (f" (index-like files present: {near[:4]})" if near else ""))
    m = p.get("membership")
    if m is not None and not Path(m).exists():
        probs.append(f"membership file missing: {m}")
    try:                                                       # the live feed needs an Upstox index key
        import refresh
        n = refresh._norm_index(p["index_symbol"])
        if n not in refresh._INDEX_KEYS:
            probs.append(f"no Upstox index key for {p['index_symbol']!r} (normalised {n!r}) - the live "
                         f"refresh cannot fetch this benchmark; set env UPSTOX_INDEX_KEY")
    except Exception:                                          # noqa: BLE001 — refresh is optional here
        pass
    return probs


def describe(name: str) -> str:
    p = get(name)
    src = Path(p["source"])
    n_csv = len(list(src.glob("*.csv"))) if src.exists() else 0
    tradeable = max(n_csv - 1, 0)                              # minus the index file
    probs = validate(name)
    head = f"{name}  —  {p['label']}"
    lines = [head, "-" * len(head),
             f"  source       : {src}",
             f"  symbols      : {tradeable} tradeable (+1 index) " + ("" if n_csv else "[FOLDER EMPTY/MISSING]"),
             f"  index_symbol : {p['index_symbol']}   (file {p['index_symbol']}.csv)",
             f"  membership   : {p['membership'] or 'none -> USE_MEMBERSHIP=False (whole folder)'}",
             f"  note         : {p.get('note', chr(8212))}"]
    ov = overrides(name)
    lines.append(f"  overrides    : " + (", ".join(f"{k}={v!r}" for k, v in sorted(ov.items()))
                                         if ov else "none -> all config.py defaults"))
    try:
        import refresh
        lines.append(f"  upstox key   : {refresh._INDEX_KEYS.get(refresh._norm_index(p['index_symbol']), '?')}")
    except Exception:                                          # noqa: BLE001
        pass
    lines.append("  status       : " + ("OK" if not probs else "PROBLEMS:"))
    lines += [f"                 !! {x}" for x in probs]
    return "\n".join(lines)


def main() -> None:
    import sys
    if len(sys.argv) > 1:
        print(describe(sys.argv[1]))
        return
    print("Universe presets (set UNIVERSE=<name> in config.py)\n")
    for n in names():
        print(describe(n)); print()
    print("Switch:  edit UNIVERSE in config.py  ->  python rebuild_all.py")


if __name__ == "__main__":
    main()
