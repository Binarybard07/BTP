"""
qbeast_backtest/universes.py
============================
Named universe presets for Backtest_Module, loaded from .env.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

__all__ = ["PRESETS", "OVERRIDABLE", "DATA_ROOT", "MEMBERSHIP_ROOT", "get", "names", "overrides", "validate", "describe"]

# Data directories loaded from .env
DATA_ROOT = Path(os.environ.get("QBEAST_DATA_ROOT", "data/Final_With_Survivorship_Data"))
MEMBERSHIP_ROOT = Path(os.environ.get("QBEAST_MEMBERSHIP_ROOT", "data/Survivorship_Bias"))

PRESETS: dict[str, dict] = {
    # --- Standard Presets (All constituents in folder) ---
    "nifty50": dict(
        label="NIFTY 50",
        source=DATA_ROOT / "NIFTY50",
        index_symbol="NIFTY50",
        membership=None,
    ),
    "nifty100": dict(
        label="NIFTY 100",
        source=DATA_ROOT / "NIFTY100",
        index_symbol="NIFTY100",
        membership=None,
    ),
    "nifty200": dict(
        label="NIFTY 200",
        source=DATA_ROOT / "NIFTY200",
        index_symbol="NIFTY200",
        membership=None,
    ),
    "nifty500": dict(
        label="NIFTY 500",
        source=DATA_ROOT / "NIFTY500",
        index_symbol="NIFTY500",
        membership=None,
    ),
    "midcap50": dict(
        label="NIFTY MidCap 50",
        source=DATA_ROOT / "NIFTYMidCap50",
        index_symbol="NIFTYMIDCAP50",
        membership=None,
    ),
    "midcap100": dict(
        label="NIFTY MidCap 100",
        source=DATA_ROOT / "NIFTYMidCap100",
        index_symbol="NIFTYMIDCAP100",
        membership=None,
    ),
    "midcap150": dict(
        label="NIFTY MidCap 150",
        source=DATA_ROOT / "NIFTYMidCap150",
        index_symbol="NIFTYMIDCAP150",
        membership=None,
    ),
    "smallcap250": dict(
        label="NIFTY SmallCap 250",
        source=DATA_ROOT / "NIFTYSmallCap250",
        index_symbol="NIFTYSMLCAP250",
        membership=None,
    ),

    # --- DDQ Presets (Point-in-time constituent membership, survivorship-bias-free) ---
    "nifty50_ddq": dict(
        label="NIFTY 50 (point-in-time membership)",
        source=DATA_ROOT / "NIFTY50",
        index_symbol="NIFTY50",
        membership=MEMBERSHIP_ROOT / "Nifty50_membership.csv",
    ),
    "nifty100_ddq": dict(
        label="NIFTY 100 (point-in-time membership)",
        source=DATA_ROOT / "NIFTY100",
        index_symbol="NIFTY100",
        membership=MEMBERSHIP_ROOT / "Nifty100_membership.csv",
    ),
    "nifty200_ddq": dict(
        label="NIFTY 200 (point-in-time membership)",
        source=DATA_ROOT / "NIFTY200",
        index_symbol="NIFTY200",
        membership=MEMBERSHIP_ROOT / "Nifty200_membership.csv",
    ),
    "nifty500_ddq": dict(
        label="NIFTY 500 (point-in-time membership)",
        source=DATA_ROOT / "NIFTY500",
        index_symbol="NIFTY500",
        membership=MEMBERSHIP_ROOT / "Nifty500_membership.csv",
    ),
    "midcap50_ddq": dict(
        label="NIFTY MidCap 50 (point-in-time membership)",
        source=DATA_ROOT / "NIFTYMidCap50",
        index_symbol="NIFTYMIDCAP50",
        membership=MEMBERSHIP_ROOT / "Midcap50_membership.csv",
    ),
    "midcap100_ddq": dict(
        label="NIFTY MidCap 100 (point-in-time membership)",
        source=DATA_ROOT / "NIFTYMidCap100",
        index_symbol="NIFTYMIDCAP100",
        membership=MEMBERSHIP_ROOT / "Midcap100_membership.csv",
    ),
    "midcap150_ddq": dict(
        label="NIFTY MidCap 150 (point-in-time membership)",
        source=DATA_ROOT / "NIFTYMidCap150",
        index_symbol="NIFTYMIDCAP150",
        membership=MEMBERSHIP_ROOT / "Midcap150_membership.csv",
    ),
    "smallcap250_ddq": dict(
        label="NIFTY SmallCap 250 (point-in-time membership)",
        source=DATA_ROOT / "NIFTYSmallCap250",
        index_symbol="NIFTYSMLCAP250",
        membership=MEMBERSHIP_ROOT / "SmallCap250_membership.csv",
    ),
}

DEFAULT = "nifty100"


def names() -> list[str]:
    return sorted(PRESETS)


def get(name: str) -> dict:
    key = str(name).strip().lower()
    if key not in PRESETS:
        raise KeyError(f"unknown UNIVERSE {name!r}. Available: {', '.join(names())}")
    return PRESETS[key]


OVERRIDABLE: dict[str, str] = {
    "top_n": "names held",
    "initial_capital": "opening capital for the backtest / paper run",
    "cost_pct": "blended transaction charge per side",
    "charges_mode": "'blended' | 'itemised'",
    "slippage_mode": "'rank' | 'dynamic' | 'fixed' | 'none'",
    "slip_min": "most liquid stock slippage",
    "slip_max": "least liquid stock slippage",
    "exec_engine": "'internal' | 'nautilus'",
    "signal_index": "index the regime/trend gate reads",
    "signal_index_dir": "folder holding signal_index.csv",
    "rebalance_freq": "monthly | weekly | quarterly",
    "use_price_bands": "circuit-lock tradability gate on/off",
    "use_price_bands_live": "live circuit-lock gate",
    "cash_yield": "net-of-tax yield on idle cash",
}


def overrides(name: str) -> dict:
    ov = dict(get(name).get("overrides") or {})
    bad = sorted(set(ov) - set(OVERRIDABLE))
    if bad:
        raise KeyError(f"universe {name!r}: unknown override(s) {bad}. Valid keys: {', '.join(sorted(OVERRIDABLE))}")
    return ov


def validate(name: str) -> list[str]:
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
        probs.append(f"index_symbol {p['index_symbol']!r} has no file {idx.name!r} in {src}")
    m = p.get("membership")
    if m is not None and not Path(m).exists():
        probs.append(f"membership file missing: {m}")
    return probs


def describe(name: str) -> str:
    p = get(name)
    src = Path(p["source"])
    n_csv = len(list(src.glob("*.csv"))) if src.exists() else 0
    tradeable = max(n_csv - 1, 0)
    probs = validate(name)
    head = f"{name} — {p['label']}"
    lines = [
        head,
        "-" * len(head),
        f"  source       : {src}",
        f"  symbols      : {tradeable} tradeable (+1 index) " + ("" if n_csv else "[EMPTY/MISSING]"),
        f"  index_symbol : {p['index_symbol']} (file {p['index_symbol']}.csv)",
        f"  membership   : {p['membership'] or 'none (whole folder)'}",
    ]
    ov = overrides(name)
    lines.append("  overrides    : " + (", ".join(f"{k}={v!r}" for k, v in sorted(ov.items())) if ov else "none"))
    lines.append("  status       : " + ("OK" if not probs else "PROBLEMS: " + "; ".join(probs)))
    return "\n".join(lines)


def main() -> None:
    import sys
    if len(sys.argv) > 1:
        print(describe(sys.argv[1]))
        return
    print("Universe presets:\n")
    for n in names():
        print(describe(n))
        print()


if __name__ == "__main__":
    main()
