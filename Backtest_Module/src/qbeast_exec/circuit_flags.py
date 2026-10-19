"""
QBEAST AI - Circuit & Liquidity Risk Flags  (v2.0.0)
=====================================================
Drop-in replacement for v1. Public function names and v1 parameters are
unchanged; everything new is keyword-only with defaults.

  1. compute_price_band_flag           - Flag 1: Price Band (attribute)
  2. compute_circuit_hit_flag          - Flag 2: Upper/Lower Circuit (EOD or INTRADAY)
  3. compute_mwcb_flag                 - Flag 3: MWCB (EOD or INTRADAY halt windows)
  4. compute_staircase_flag            - Flag 4: Staircase (EOD detect, INTRADAY propagate)
  5. compute_chronic_illiquidity_flag  - NEW   : chronic illiquidity (EOD)
     aggregate_mwcb_by_date            - NEW   : one MWCB row per date (NIFTY/SENSEX max)
     apply_mwcb_windows                - NEW   : stamp MWCB halt windows onto stock bars
     build_universe_exclusion_table    - NEW   : SPEC Sec 6 table + trade/fill gates
     CircuitLiquidityTracker           - NEW   : streaming bar-by-bar (EOD + INTRADAY)
     four_flag_columns                 - NEW   : one readable column per flag
                                                 (PRICE_BAND, CIRCUIT, MWCB, STAIRCASE)

CAUSALITY CONTRACT (No Forward Bias, Causal Iterative Flow)
-----------------------------------------------------------
* Every output on row t is computed from rows <= t of the same symbol only.
  No retro-marking, no back-dated ends, no centred windows.
* Row t's flags describe the state "as of the close of bar t". A strategy
  deciding at bar t reads row t; its order fills at bar t+1's open.
* Batch functions and CircuitLiquidityTracker run the SAME per-bar step
  objects, so a full-history batch call, an expanding-window call per bar,
  and streaming bar-by-bar produce identical values (tests enforce this,
  plus a truncation test: cutting data at t never changes any row <= t).
* The only columns that describe a bar's OPEN (open_at_upper/open_at_lower,
  *_fill_feasible_at_open) are for the execution SIMULATOR, which reads them
  from the fill bar t+1 -- exactly what the exchange would do to a live
  order. The strategy must never read them from t+1.

TRADING RULE (confirmed with owner, 2026-09-26)
-----------------------------------------------
While a symbol is tagged (staircase / chronic illiquid / MWCB halt):
no new buy, no new sell, no exit of an existing position; MtM continues.
Untag happens only causally (E consecutive NORMAL days with real volume for
staircase; hysteresis thresholds for chronic).

Python 3.10+. Dependencies: pandas, numpy.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Iterable, Literal, Sequence

import numpy as np
import pandas as pd

__version__ = "2.4.1"
logger = logging.getLogger("qbeast.flags.circuit_liquidity")

BAND_CANDIDATES: tuple[float, ...] = (2.0, 5.0, 10.0, 20.0)
NO_BAND = math.inf  # use np.inf in band columns for "no band" (e.g. F&O stocks)

DORMANT, BUILDING, CONFIRMED, COOLING = "DORMANT", "BUILDING", "CONFIRMED", "COOLING"
LADDER, NORMAL, OTHER, UNKNOWN = "LADDER", "NORMAL", "OTHER", "UNKNOWN"
_DIR_LABEL = {"upper": "climbing", "lower": "falling"}


# ============================================================================
# Exceptions & validation helpers
# ============================================================================

class FlagInputError(ValueError):
    """Raised when input data or parameters fail validation. Subclasses
    ValueError so existing `except ValueError` call sites still catch it."""


def _require_nonempty(df: pd.DataFrame, fn_name: str) -> None:
    if df is None or len(df) == 0:
        raise FlagInputError(f"{fn_name}: input DataFrame is empty or None.")


def _require_columns(df: pd.DataFrame, required: set[str], fn_name: str) -> None:
    missing = required - set(df.columns)
    if missing:
        raise FlagInputError(
            f"{fn_name}: missing required column(s): {sorted(missing)}. "
            f"Columns present: {sorted(df.columns)}"
        )


def _require_no_duplicate_keys(df: pd.DataFrame, keys: list[str], fn_name: str) -> None:
    dupes = df.duplicated(subset=keys, keep=False)
    if dupes.any():
        sample = df.loc[dupes, keys].head(5).to_dict("records")
        raise FlagInputError(
            f"{fn_name}: {int(dupes.sum())} duplicate row(s) for key {keys}. Sample: {sample}"
        )


def parse_dates(s: pd.Series) -> pd.Series:
    """ISO strings (YYYY-MM-DD...) parsed as ISO; anything else day-first
    (Indian dd-mm-yyyy). pandas' dayfirst=True silently swaps day/month on
    ISO strings ('2024-03-12' -> 3 Dec), so the two cases must be split."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    txt = s.astype(str).str.strip()
    iso = txt.str.match(r"^\d{4}-\d{2}-\d{2}")
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if iso.any():
        out[iso] = pd.to_datetime(txt[iso], format="ISO8601")
    if (~iso).any():
        out[~iso] = pd.to_datetime(txt[~iso], dayfirst=True)
    return out


def _coerce_datetime(df: pd.DataFrame, col: str, fn_name: str) -> pd.DataFrame:
    """Parse to tz-naive datetime64[ns] (one resolution everywhere, so
    merges between EOD and intraday frames never hit ns/us mismatches)."""
    df = df.copy()
    try:
        s = parse_dates(df[col])
        if getattr(s.dt, "tz", None) is not None:
            s = s.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        df[col] = s.astype("datetime64[ns]")
    except (ValueError, TypeError) as exc:
        raise FlagInputError(f"{fn_name}: could not parse column '{col}' as datetime: {exc}") from exc
    return df


def _warn_on_nulls(df: pd.DataFrame, cols: list[str], fn_name: str) -> None:
    for c in cols:
        if c in df.columns:
            n_null = int(df[c].isna().sum())
            if n_null:
                logger.warning("%s: column '%s' has %d null value(s) out of %d row(s).",
                               fn_name, c, n_null, len(df))


def _prep(df: pd.DataFrame, fn: str, symbol_col: str, date_col: str, required: set[str]) -> pd.DataFrame:
    _require_nonempty(df, fn)
    _require_columns(df, required | {symbol_col, date_col}, fn)
    out = _coerce_datetime(df.copy(), date_col, fn)
    for k in (symbol_col, date_col):
        n_null = int(out[k].isna().sum())
        if n_null:
            raise FlagInputError(f"{fn}: column '{k}' has {n_null} null value(s); every row needs a key.")
    _require_no_duplicate_keys(out, [symbol_col, date_col], fn)
    return out.sort_values([symbol_col, date_col], kind="mergesort").reset_index(drop=True)


def _num(x: Any) -> bool:
    """True if x is a real number (not None / NaN). inf counts as a number."""
    if x is None:
        return False
    try:
        return not math.isnan(x)
    except TypeError:
        return False


def _col(df: pd.DataFrame, name: str | None, default: Any = np.nan) -> np.ndarray:
    if name is not None and name in df.columns:
        return df[name].to_numpy()
    return np.full(len(df), default, dtype=object if not isinstance(default, float) else float)


def _group_slices(sym: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous [start, end) slices per symbol of a symbol-sorted array."""
    n = len(sym)
    if n == 0:
        return []
    cuts = np.flatnonzero(sym[1:] != sym[:-1]) + 1
    starts = np.r_[0, cuts]
    ends = np.r_[cuts, n]
    return list(zip(starts.tolist(), ends.tolist()))


def _assign(out: pd.DataFrame, rows: list[dict], keys: Sequence[str]) -> pd.DataFrame:
    if not rows:
        return out
    block = pd.DataFrame.from_records(rows, columns=list(keys))
    for k in keys:
        out[k] = block[k].to_numpy()
    return out


# ============================================================================
# Parameter objects
# ============================================================================

@dataclass(frozen=True)
class CircuitParams:
    """Flag 2 parameters (EOD + INTRADAY). All tolerances are percentages of
    prev_close unless stated otherwise."""
    tol: float = 1e-4                    # extra relative slack (fraction) on top of 1 tick
    tick_size: float = 0.05              # exchange tick (Rs). Slack = tick/prev_close + tol
    lock_mode: Literal["strict", "close_locked"] = "close_locked"
    band_candidates: tuple[float, ...] = BAND_CANDIDATES
    infer_band: bool = True              # causal band inference when band columns absent/NaN
    band_lookback: int = 20              # sessions of range history bounding an inferred band
    price_eq_rel_tol: float = 1e-6       # relative tolerance for price equality (O==H etc.)
    min_band_ticks: float = 2.0          # band narrower than this many ticks -> tick_degenerate
    fine_tick_size: float = 0.01         # tick assumed when recent prices leave the tick_size grid
    tick_grid_lookback: int = 10         # closes used to detect the finer grid (causal)
    # intraday
    intraday_min_pinned_bars: int = 3
    intraday_vol_frac: float = 0.10
    intraday_vol_lookback: int = 20
    intraday_require_thin_vol: bool = False
    intraday_candidate_mode: bool = False   # match any candidate band intraday when the session band is unknown
    offgrid_max_slack_pct: float = 0.5      # off-grid (adjusted / fine-tick) prices: cap on the coarse-tick slack

    def validate(self, fn: str) -> None:
        if self.tick_size <= 0:
            raise FlagInputError(f"{fn}: tick_size must be > 0, got {self.tick_size}.")
        if self.tol < 0:
            raise FlagInputError(f"{fn}: tol must be >= 0, got {self.tol}.")
        if self.lock_mode not in ("strict", "close_locked"):
            raise FlagInputError(f"{fn}: lock_mode must be 'strict' or 'close_locked'.")
        if self.intraday_min_pinned_bars < 1:
            raise FlagInputError(f"{fn}: intraday_min_pinned_bars must be >= 1, got {self.intraday_min_pinned_bars}.")
        if not (0 < self.intraday_vol_frac <= 1):
            raise FlagInputError(f"{fn}: intraday_vol_frac must be in (0, 1], got {self.intraday_vol_frac}.")
        if self.intraday_vol_lookback < 1 or self.band_lookback < 1:
            raise FlagInputError(f"{fn}: lookbacks must be >= 1.")
        if not self.band_candidates or any(b <= 0 for b in self.band_candidates):
            raise FlagInputError(f"{fn}: band_candidates must be positive percentages.")


@dataclass(frozen=True)
class StaircaseParams:
    """Flag 4 parameters. v1 field names kept.

    start_confirm_days (S): consecutive same-direction ladder days to tag.
    end_confirm_days   (E): consecutive NORMAL days needed to untag
                            (run ends when the count REACHES E, per SPEC 3.4).
    vol_frac: optional thin-volume requirement for a LADDER day (volume <
              vol_frac x baseline). Default None = not required: real data
              (INDOTHAI 23-Dec-2021, frozen at +5% on the window's highest
              volume) shows locked days can print heavy volume.
    volume_lookback: number of CLEAN sessions (not circuit, not single-price,
              not unknown) in the volume baseline -- counted in observations,
              not calendar rows, so long ladders cannot starve it.
    normal_vol_frac: a NORMAL (untagging) day needs volume >= this x baseline
              ("volumes are there").
    min_baseline_obs: observations needed before the baseline is trusted
              (default max(3, volume_lookback // 4)).
    """
    start_confirm_days: int = 5
    end_confirm_days: int = 2
    vol_frac: float | None = None
    volume_lookback: int = 20
    normal_vol_frac: float = 0.5
    min_baseline_obs: int | None = None
    intraday_early_block: bool = True

    @property
    def min_obs(self) -> int:
        return self.min_baseline_obs if self.min_baseline_obs is not None else max(3, self.volume_lookback // 4)

    def validate(self, fn: str) -> None:
        if self.start_confirm_days < 1:
            raise FlagInputError(f"{fn}: start_confirm_days must be >= 1, got {self.start_confirm_days}.")
        if self.end_confirm_days < 1:
            raise FlagInputError(f"{fn}: end_confirm_days must be >= 1, got {self.end_confirm_days}.")
        if self.vol_frac is not None and not (0 < self.vol_frac <= 1):
            raise FlagInputError(f"{fn}: vol_frac must be None or in (0, 1], got {self.vol_frac}.")
        if self.volume_lookback < 1:
            raise FlagInputError(f"{fn}: volume_lookback must be >= 1, got {self.volume_lookback}.")
        if not (0 < self.normal_vol_frac <= 10):
            raise FlagInputError(f"{fn}: normal_vol_frac must be in (0, 10], got {self.normal_vol_frac}.")
        if self.min_obs < 1 or self.min_obs > self.volume_lookback:
            raise FlagInputError(f"{fn}: min_baseline_obs must be in [1, volume_lookback].")


@dataclass(frozen=True)
class ChronicParams:
    """Chronic illiquidity (new). Trailing window of sessions, hysteresis
    (enter >= enter_*, exit only when every metric < its exit_*)."""
    window: int = 60
    min_obs: int = 20
    enter_single_price: float = 0.20
    exit_single_price: float = 0.10
    enter_no_trade: float = 0.20
    exit_no_trade: float = 0.10
    enter_circuit: float = 0.30
    exit_circuit: float = 0.15

    def validate(self, fn: str) -> None:
        if self.window < 1 or not (1 <= self.min_obs <= self.window):
            raise FlagInputError(f"{fn}: need window >= 1 and 1 <= min_obs <= window.")
        for a, b in ((self.enter_single_price, self.exit_single_price),
                     (self.enter_no_trade, self.exit_no_trade),
                     (self.enter_circuit, self.exit_circuit)):
            if not (0 < b <= a <= 1):
                raise FlagInputError(f"{fn}: need 0 < exit <= enter <= 1 for every chronic threshold.")


# MWCB halt rules (current SEBI regime). Each entry: (level, [(until_hhmm, halt_minutes|None=rest of day|0=no halt)])
DEFAULT_MWCB_HALT_RULES: dict[int, list[tuple[str, int | None]]] = {
    10: [("13:00", 45), ("14:30", 15), ("24:00", 0)],
    15: [("13:00", 105), ("14:00", 45), ("24:00", None)],
    20: [("24:00", None)],
}


# ============================================================================
# Per-bar step objects (single source of truth for batch AND streaming)
# ============================================================================

def _slack_pct(p: "CircuitParams", pc: float, eff_tick: float) -> float:
    """Matching tolerance in % of prev_close.
    On the coarse grid (raw 0.05-tick prices): one coarse tick.
    Off the grid (fine-tick era, or back-adjusted history): the coarse-tick
    slack capped at max(one fine tick, offgrid_max_slack_pct) -- otherwise a
    back-adjusted Rs 1.5 price turns a 0.05 tick into a 3% window that swallows
    ordinary moves (AARTIIND 2000-2003 in the full run)."""
    coarse = p.tick_size / pc * 100.0
    if eff_tick < p.tick_size:
        coarse = min(coarse, max(eff_tick / pc * 100.0, p.offgrid_max_slack_pct))
    return coarse + p.tol * 100.0


class _CircuitEodStep:
    """Causal per-session circuit detector with tick-aware band matching and
    causal band inference (per exchange). One instance per symbol."""

    OUT_KEYS = (
        "prev_close_used", "move_pct", "slack_pct", "frozen", "band_upper_used",
        "band_lower_used", "band_source", "band_hit_dir", "circuit_locked_dir",
        "upside_locked", "open_at_upper", "open_at_lower", "single_price",
        "band_breach", "data_unknown", "band_est_next", "effective_tick", "tick_degenerate",
    )

    def __init__(self, p: CircuitParams):
        self.p = p
        self.prev_close: float | None = None
        self.prev_exch: Any = None
        self.band_state: dict[Any, list] = {}
        self.grid: deque = deque(maxlen=p.tick_grid_lookback)

    def _on_grid(self, x: float) -> bool:
        q = x / self.p.tick_size
        return abs(q - round(q)) < 1e-6

    def _st(self, exch: Any) -> list:
        st = self.band_state.get(exch)
        if st is None:
            st = self.band_state[exch] = [None, deque(maxlen=self.p.band_lookback)]
        return st

    def _eq(self, a: float, b: float) -> bool:
        return abs(a - b) <= self.p.price_eq_rel_tol * max(abs(a), abs(b)) + 1e-12

    @staticmethod
    def _match(x: float, bands: Iterable[float], s: float) -> float | None:
        best, dist = None, None
        for b in bands:
            d = abs(x - b)
            if d <= s and (dist is None or d < dist):
                best, dist = b, d
        return best

    def step(self, o, h, l, c, v, pc_given, exch, gap, ub, lb) -> dict:
        p = self.p
        derived = not _num(pc_given)
        pc = self.prev_close if derived else float(pc_given)
        exch_switch = (derived and exch is not None and self.prev_exch is not None
                       and exch == exch and self.prev_exch == self.prev_exch and exch != self.prev_exch)
        # effective tick from closes <= t (causal): finer grid once prices leave the coarse grid
        if _num(c) and not bool(gap):
            self.grid.append(self._on_grid(float(c)))
        eff_tick = p.tick_size if (self.grid and all(self.grid)) else p.fine_tick_size
        # advance memory for next bar (only real prints update prev_close)
        if _num(c) and not bool(gap):
            self.prev_close = float(c)
        if exch is not None and exch == exch:
            self.prev_exch = exch

        r = dict.fromkeys(self.OUT_KEYS)
        r.update(frozen=False, upside_locked=False, open_at_upper=False, open_at_lower=False,
                 single_price=False, band_breach=False, band_hit_dir=None, circuit_locked_dir=None,
                 band_source=None, move_pct=np.nan, slack_pct=np.nan, band_upper_used=np.nan,
                 band_lower_used=np.nan, prev_close_used=pc if pc is not None else np.nan,
                 effective_tick=eff_tick, tick_degenerate=False)
        st = self._st(exch)
        r["band_est_next"] = st[0] if st[0] is not None else np.nan

        if not all(_num(x) for x in (o, h, l, c)):
            r["data_unknown"] = True
            return r
        frozen = self._eq(o, h) and self._eq(h, l) and self._eq(l, c)
        r["frozen"] = frozen
        if bool(gap) or exch_switch or pc is None or not _num(pc) or pc <= 0:
            r["data_unknown"] = True
            return r
        r["data_unknown"] = False

        m = (c / pc - 1.0) * 100.0
        om = (o / pc - 1.0) * 100.0
        rng = max(h / pc - 1.0, 1.0 - l / pc) * 100.0
        # slack from the EFFECTIVE tick: 0.05 while closes sit on the 0.05 grid,
        # 0.01 once they leave it (fine-tick era, or back-adjusted history whose
        # tiny adjusted prices would otherwise inflate a 0.05 slack to several %)
        s = _slack_pct(p, pc, eff_tick)
        r["move_pct"], r["slack_pct"] = m, s
        if p.lock_mode == "strict":
            lk_up = lk_dn = frozen
        else:
            lk_up, lk_dn = self._eq(c, h), self._eq(c, l)

        up_b = dn_b = None
        given = _num(ub) and _num(lb)
        if given:
            ub, lb = float(ub), float(lb)
            r["band_source"] = "given"
            r["band_upper_used"], r["band_lower_used"] = ub, lb
            if ub == 0:
                r["upside_locked"] = m >= -s
                r["open_at_upper"] = om >= -s
            elif math.isfinite(ub):
                if m > 0 and abs(m - ub) <= s:
                    up_b = ub
                r["open_at_upper"] = abs(om - ub) <= s
                r["band_breach"] = m > ub + s
            if lb > 0 and math.isfinite(lb):
                if m < 0 and abs(-m - lb) <= s:
                    dn_b = lb
                r["open_at_lower"] = abs(-om - lb) <= s
                r["band_breach"] = r["band_breach"] or (-m > lb + s)
        elif p.infer_band:
            est = st[0]
            lbound = max(st[1]) if st[1] else 0.0
            cands = [b for b in p.band_candidates if b >= lbound - s]
            r["band_source"] = "inferred" if est is not None else "candidate"
            if est is not None and math.isfinite(est):
                if m > 0 and abs(m - est) <= s:
                    up_b = est
                elif m > 0 and lk_up:   # tightening evidence: locked at a smaller band
                    up_b = self._match(m, [b for b in p.band_candidates if b < est], s)
                if m < 0 and abs(-m - est) <= s:
                    dn_b = est
                elif m < 0 and lk_dn:
                    dn_b = self._match(-m, [b for b in p.band_candidates if b < est], s)
            elif est is None:
                if m > 0 and lk_up:
                    up_b = self._match(m, p.band_candidates if frozen else cands, s)
                if m < 0 and lk_dn:
                    dn_b = self._match(-m, p.band_candidates if frozen else cands, s)
            ref_u = up_b if up_b is not None else est
            ref_d = dn_b if dn_b is not None else est
            r["band_upper_used"] = ref_u if ref_u is not None else np.nan
            r["band_lower_used"] = ref_d if ref_d is not None else np.nan
            if ref_u is not None and math.isfinite(ref_u):
                r["open_at_upper"] = abs(om - ref_u) <= s
            if ref_d is not None and math.isfinite(ref_d):
                r["open_at_lower"] = abs(-om - ref_d) <= s
            if est is not None and math.isfinite(est) and up_b is None and dn_b is None:
                r["band_breach"] = rng > est + s

        hit_b = up_b if up_b is not None else dn_b
        if hit_b is not None and math.isfinite(hit_b):
            # band spans fewer than min_band_ticks ticks -> every one-tick close
            # at the extreme "hits" it (e.g. Rs 1.05 stock, 5% band = 1 tick)
            r["tick_degenerate"] = hit_b / 100.0 * pc < p.min_band_ticks * eff_tick - 1e-12
        if up_b is not None:
            r["band_hit_dir"] = "upper"
            if lk_up:
                r["circuit_locked_dir"] = "upper"
        elif dn_b is not None:
            r["band_hit_dir"] = "lower"
            if lk_dn:
                r["circuit_locked_dir"] = "lower"
        r["single_price"] = frozen and r["band_hit_dir"] is None and not r["upside_locked"]

        # ---- causal band-state update (uses bars <= t only) ----
        if not given and p.infer_band:
            if r["circuit_locked_dir"] == "upper":
                st[0] = up_b
            elif r["circuit_locked_dir"] == "lower":
                st[0] = dn_b
            elif st[0] is not None and math.isfinite(st[0]) and rng > st[0] + s:
                st[0] = None          # band evidently wider than believed -> unknown
            st[1].append(rng)
            r["band_est_next"] = st[0] if st[0] is not None else np.nan
        return r


class _CircuitIntradayStep:
    """Causal per-bar pinned-at-band detector. Runs of pinned bars reset at
    every session boundary."""

    OUT_KEYS = (
        "session_prev_close", "band_upper_used", "band_lower_used", "band_hit_dir",
        "upside_locked", "pinned", "pinned_run_len", "thin_vol", "intraday_freeze",
        "freeze_dir", "locked_since_open_dir", "open_at_upper", "open_at_lower", "data_unknown",
    )

    def __init__(self, p: CircuitParams):
        self.p = p
        self.session = None
        self.run = 0
        self.run_dir = None
        self.since_open = None   # dir if every bar so far this session pinned in dir; False otherwise
        self.vols: deque = deque(maxlen=p.intraday_vol_lookback)
        self.grid: deque = deque(maxlen=p.tick_grid_lookback)
        self.sess_hi = None
        self.sess_lo = None

    def _eq(self, a, b):
        return abs(a - b) <= self.p.price_eq_rel_tol * max(abs(a), abs(b)) + 1e-12

    def step(self, session, o, h, l, c, v, pc, ub, lb) -> dict:
        p = self.p
        if session != self.session:
            self.session, self.run, self.run_dir, self.since_open = session, 0, None, None
            self.sess_hi = self.sess_lo = None
        if _num(h):
            self.sess_hi = h if self.sess_hi is None else max(self.sess_hi, h)
        if _num(l):
            self.sess_lo = l if self.sess_lo is None else min(self.sess_lo, l)
        if _num(c):
            q = c / p.tick_size
            self.grid.append(abs(q - round(q)) < 1e-6)
        r = dict.fromkeys(self.OUT_KEYS)
        r.update(session_prev_close=pc if _num(pc) else np.nan, band_upper_used=ub, band_lower_used=lb,
                 band_hit_dir=None, upside_locked=False, pinned=False, thin_vol=False,
                 intraday_freeze=False, freeze_dir=None, open_at_upper=False, open_at_lower=False)
        if not all(_num(x) for x in (o, h, l, c)) or not _num(pc) or pc <= 0:
            r.update(data_unknown=True, pinned_run_len=0, locked_since_open_dir=None)
            self.run, self.run_dir, self.since_open = 0, None, False
            return r
        r["data_unknown"] = False
        m = (c / pc - 1.0) * 100.0
        om = (o / pc - 1.0) * 100.0
        eff_tick = p.tick_size if (self.grid and all(self.grid)) else p.fine_tick_size
        s = _slack_pct(p, pc, eff_tick)
        # a real circuit cannot have traded beyond the band earlier this session
        hi_pct = (self.sess_hi / pc - 1.0) * 100.0 if self.sess_hi is not None else -np.inf
        lo_pct = (1.0 - self.sess_lo / pc) * 100.0 if self.sess_lo is not None else -np.inf
        frozen = self._eq(o, h) and self._eq(h, l) and self._eq(l, c)
        lk_up = frozen if p.lock_mode == "strict" else self._eq(c, h)
        lk_dn = frozen if p.lock_mode == "strict" else self._eq(c, l)
        d = None
        if _num(ub) and _num(lb):
            if ub == 0:
                r["upside_locked"] = m >= -s
                r["open_at_upper"] = om >= -s
            elif math.isfinite(ub):
                if m > 0 and abs(m - ub) <= s and lk_up and hi_pct <= ub + s:
                    d = "upper"
                r["open_at_upper"] = abs(om - ub) <= s
            if lb > 0 and math.isfinite(lb):
                if d is None and m < 0 and abs(-m - lb) <= s and lk_dn and lo_pct <= lb + s:
                    d = "lower"
                r["open_at_lower"] = abs(-om - lb) <= s
        elif p.infer_band and p.intraday_candidate_mode:  # band unknown -> any candidate (opt-in)
            if m > 0 and lk_up and _CircuitEodStep._match(m, p.band_candidates, s) is not None:
                d = "upper"
                r["open_at_upper"] = _CircuitEodStep._match(om, p.band_candidates, s) is not None
            elif m < 0 and lk_dn and _CircuitEodStep._match(-m, p.band_candidates, s) is not None:
                d = "lower"
                r["open_at_lower"] = _CircuitEodStep._match(-om, p.band_candidates, s) is not None
        r["band_hit_dir"] = d
        pinned = d is not None
        r["pinned"] = pinned
        if pinned and d == self.run_dir:
            self.run += 1
        elif pinned:
            self.run, self.run_dir = 1, d
        else:
            self.run, self.run_dir = 0, None
        if self.since_open is None:
            self.since_open = d if pinned else False
        elif self.since_open is not False and self.since_open != d:
            self.since_open = False
        base = median(self.vols) if len(self.vols) >= max(3, p.intraday_vol_lookback // 4) else None
        thin = bool(base is not None and _num(v) and v < p.intraday_vol_frac * base)
        if not pinned and _num(v):
            self.vols.append(float(v))
        r["thin_vol"] = thin
        r["pinned_run_len"] = self.run
        r["locked_since_open_dir"] = self.since_open if self.since_open else None
        freeze = pinned and self.run >= p.intraday_min_pinned_bars and (thin or not p.intraday_require_thin_vol)
        r["intraday_freeze"] = freeze
        r["freeze_dir"] = d if freeze else None
        return r


class _StaircaseStep:
    """Causal DORMANT -> BUILDING -> CONFIRMED <-> COOLING state machine.
    Tagged = CONFIRMED or COOLING. Untag only after E consecutive NORMAL days."""

    OUT_KEYS = (
        "day_class", "vol_baseline", "thin_vol", "ladder_day", "staircase_state",
        "staircase", "staircase_dir", "ladder_run_len", "normal_day_count",
        "staircase_event_id", "staircase_early_block_dir",
    )

    def __init__(self, p: StaircaseParams):
        self.p = p
        self.state = DORMANT
        self.dir = None
        self.run = 0
        self.normal = 0
        self.event = 0
        self.seg = object()
        self.vols: deque = deque(maxlen=p.volume_lookback)

    def step(self, locked_dir, upside_locked, unknown, single_price, volume, seg_key) -> dict:
        p = self.p
        if seg_key != self.seg:
            self.seg = seg_key
            self.vols.clear()
        base = median(self.vols) if len(self.vols) >= p.min_obs else None
        vol_ok = _num(volume)
        thin = bool(base is not None and vol_ok and volume < (p.vol_frac or 0.0) * base) if p.vol_frac else None
        has_dir = locked_dir is not None and locked_dir == locked_dir
        if unknown:
            cls = UNKNOWN
        elif has_dir and not upside_locked and (p.vol_frac is None or thin):
            cls = LADDER
        elif (not has_dir and not single_price and not upside_locked and base is not None
              and vol_ok and volume > 0 and volume >= p.normal_vol_frac * base):
            cls = NORMAL
        else:
            cls = OTHER

        S, E = p.start_confirm_days, p.end_confirm_days
        if cls == UNKNOWN:
            pass
        elif self.state == DORMANT:
            if cls == LADDER:
                self.state, self.dir, self.run = BUILDING, locked_dir, 1
        elif self.state == BUILDING:
            if cls == LADDER:
                if locked_dir == self.dir:
                    self.run += 1
                else:
                    self.dir, self.run = locked_dir, 1
            else:
                self.state, self.dir, self.run = DORMANT, None, 0
        else:  # CONFIRMED / COOLING (tagged)
            if cls == LADDER:
                if locked_dir != self.dir:      # flip: stays tagged, new event
                    self.dir, self.run = locked_dir, 1
                    self.event += 1
                else:
                    self.run += 1
                self.state, self.normal = CONFIRMED, 0
            elif cls == NORMAL:
                self.normal += 1
                self.state = COOLING
                if self.normal >= E:
                    self.state, self.dir, self.run, self.normal = DORMANT, None, 0, 0
            else:
                self.normal = 0
        if self.state == BUILDING and self.run >= S:
            self.state, self.normal = CONFIRMED, 0
            self.event += 1

        if cls in (NORMAL, OTHER) and not has_dir and not single_price and vol_ok and volume > 0:
            self.vols.append(float(volume))

        tagged = self.state in (CONFIRMED, COOLING)
        return {
            "day_class": cls,
            "vol_baseline": base if base is not None else np.nan,
            "thin_vol": bool(thin) if thin is not None else False,
            "ladder_day": cls == LADDER,
            "staircase_state": self.state,
            "staircase": tagged,
            "staircase_dir": _DIR_LABEL.get(self.dir) if tagged else None,
            "ladder_run_len": self.run if self.state != DORMANT else 0,
            "normal_day_count": self.normal,
            "staircase_event_id": self.event if tagged else 0,
            "staircase_early_block_dir": (self.dir if (self.state == BUILDING and self.run == S - 1) else None),
        }


class _ChronicStep:
    OUT_KEYS = ("single_price_frac", "no_trade_frac", "circuit_frac", "chronic_obs", "chronic_illiquid")

    def __init__(self, p: ChronicParams):
        self.p = p
        self.win: deque = deque(maxlen=p.window)
        self.tagged = False

    def step(self, single_price: bool, no_trade: bool, circuit: bool, missing_sessions: int = 0) -> dict:
        p = self.p
        for _ in range(min(max(int(missing_sessions), 0), p.window)):
            self.win.append((0, 1, 0))
        self.win.append((int(bool(single_price)), int(bool(no_trade)), int(bool(circuit))))
        n = len(self.win)
        if n >= p.min_obs:
            sp = sum(x[0] for x in self.win) / n
            nt = sum(x[1] for x in self.win) / n
            cf = sum(x[2] for x in self.win) / n
            if not self.tagged:
                self.tagged = sp >= p.enter_single_price or nt >= p.enter_no_trade or cf >= p.enter_circuit
            else:
                self.tagged = not (sp < p.exit_single_price and nt < p.exit_no_trade and cf < p.exit_circuit)
        else:
            sp = nt = cf = np.nan
        return {"single_price_frac": sp, "no_trade_frac": nt, "circuit_frac": cf,
                "chronic_obs": n, "chronic_illiquid": self.tagged}


# ============================================================================
# Flag 1 - Price Band
# ============================================================================

def compute_price_band_flag(
    df: pd.DataFrame,
    *,
    symbol_col: str = "symbol",
    date_col: str = "date",
    upper_col: str = "upper_band_pct",
    lower_col: str = "lower_band_pct",
) -> pd.DataFrame:
    """
    Flag 1 - Price Band (point-in-time attribute supplied by the caller).

    Band values: 0-100 (%), np.inf = "no band" (F&O), NaN = unknown.
    Returns a copy sorted by (symbol, date) with:
        band_known      : bool  both sides known (number or inf)
        band_asymmetric : bool  known and upper != lower (NaN/NaN is NOT asymmetric)
        band_tightened  : bool  either side narrower than its last KNOWN value
                                (inf -> 20 counts; unknown rows are skipped)
        band_widened    : bool  either side wider than its last known value
    Causal: compares only with earlier rows of the same symbol.
    """
    fn = "compute_price_band_flag"
    out = _prep(df, fn, symbol_col, date_col, {upper_col, lower_col})
    _warn_on_nulls(out, [upper_col, lower_col], fn)
    for col in (upper_col, lower_col):
        vals = pd.to_numeric(out[col], errors="coerce")
        bad = vals.notna() & ~np.isinf(vals) & ((vals < 0) | (vals > 100))
        if bad.any():
            raise FlagInputError(f"{fn}: column '{col}' has {int(bad.sum())} value(s) outside [0, 100] "
                                 "(use np.inf for 'no band', NaN for unknown).")
        out[col] = vals.astype(float)
    u, lo = out[upper_col], out[lower_col]
    out["band_known"] = u.notna() & lo.notna()
    out["band_asymmetric"] = out["band_known"] & (u != lo)
    g = out.groupby(symbol_col, sort=False)
    prev_u = g[upper_col].transform(lambda s: s.ffill().shift())
    prev_l = g[lower_col].transform(lambda s: s.ffill().shift())
    out["band_tightened"] = ((u < prev_u) | (lo < prev_l)).fillna(False).astype(bool)
    out["band_widened"] = ((u > prev_u) | (lo > prev_l)).fillna(False).astype(bool)
    return out


# ============================================================================
# Flag 2 - Upper/Lower Circuit
# ============================================================================

def _circuit_params(**kw) -> CircuitParams:
    return CircuitParams(**{k: v for k, v in kw.items() if v is not None})


def _session_key(ts: pd.Series) -> pd.Series:
    return ts.dt.normalize()


def compute_circuit_hit_flag(
    df: pd.DataFrame,
    *,
    mode: Literal["eod", "intraday"] = "eod",
    symbol_col: str = "symbol",
    date_col: str = "date",
    upper_pct_col: str = "upper_band_pct",
    lower_pct_col: str = "lower_band_pct",
    tol: float = 1e-4,
    intraday_min_pinned_bars: int = 3,
    intraday_vol_frac: float = 0.10,
    intraday_vol_lookback: int = 20,
    # ---- new in v2 (keyword-only, defaulted) ----
    prev_close_col: str = "prev_close",
    exchange_col: str = "exchange",
    gap_filled_col: str = "_gap_filled",
    tick_size: float = 0.05,
    lock_mode: Literal["strict", "close_locked"] = "close_locked",
    band_candidates: tuple[float, ...] = BAND_CANDIDATES,
    infer_band: bool = True,
    band_lookback: int = 20,
    intraday_require_thin_vol: bool = False,
    intraday_candidate_mode: bool = False,
    circuit_params: CircuitParams | None = None,
) -> pd.DataFrame:
    """
    Flag 2 - Upper/Lower Circuit. Causal: row t uses rows <= t only.

    Required columns: symbol, date, open, high, low, close, volume.
    Optional: prev_close (else derived: previous row's close in EOD, previous
    session's last close in INTRADAY), upper/lower_band_pct (else causal
    inference), exchange (EOD: a bar right after an exchange switch is
    data_unknown because prev_close came from another exchange),
    _gap_filled (forward-filled rows are data_unknown).

    Matching is tick-aware: a close matches band b when
    |move% - b| <= tick/prev_close*100 + tol*100 (NSE floors limits to tick,
    e.g. 5% on Rs 1.70 = 2.94%).

    EOD output columns: prev_close_used, move_pct, slack_pct, frozen,
      band_upper_used, band_lower_used, band_source ('given'/'inferred'/
      'candidate'), band_hit_dir ('upper'/'lower'/NaN; close at the boundary),
      circuit_locked_dir (band hit AND locked per lock_mode: 'strict' =
      O=H=L=C, 'close_locked' = close==high for upper / close==low for lower),
      upside_locked, open_at_upper, open_at_lower, single_price (frozen but not
      at a band), band_breach, data_unknown, band_est_next (band believed for
      the NEXT session, from bars <= t).

    INTRADAY output columns: session_prev_close, band_upper_used,
      band_lower_used, band_hit_dir, upside_locked, pinned, pinned_run_len
      (resets each session), thin_vol, intraday_freeze, freeze_dir,
      locked_since_open_dir, open_at_upper, open_at_lower, data_unknown.
      Session band: given columns, else the causal EOD inference over the
      previous completed sessions' aggregates (never the current session).

    circuit_params: a complete CircuitParams object (e.g. built from the
      config file by flags_config.load_config). When given it overrides all
      the individual keyword parameters above.
    """
    fn = "compute_circuit_hit_flag"
    if mode not in ("eod", "intraday"):
        raise FlagInputError(f"{fn}: mode must be 'eod' or 'intraday', got {mode!r}.")
    if circuit_params is not None:           # a full parameter object (e.g. from the config file) wins
        p = circuit_params
    else:
        p = CircuitParams(tol=tol, tick_size=tick_size, lock_mode=lock_mode, band_candidates=tuple(band_candidates),
                          infer_band=infer_band, band_lookback=band_lookback,
                          intraday_min_pinned_bars=intraday_min_pinned_bars, intraday_vol_frac=intraday_vol_frac,
                          intraday_vol_lookback=intraday_vol_lookback,
                          intraday_require_thin_vol=intraday_require_thin_vol,
                          intraday_candidate_mode=intraday_candidate_mode)
    p.validate(fn)
    out = _prep(df, fn, symbol_col, date_col, {"open", "high", "low", "close", "volume"})
    price_cols = ["open", "high", "low", "close"] + ([prev_close_col] if prev_close_col in out.columns else [])
    _warn_on_nulls(out, price_cols + ["volume"], fn)
    non_positive = (out[price_cols] <= 0).any(axis=1)
    if non_positive.any():
        raise FlagInputError(f"{fn}: {int(non_positive.sum())} row(s) have a non-positive OHLC/prev_close price.")

    if mode == "eod":
        rows = _run_circuit_eod(out, p, symbol_col, prev_close_col, exchange_col, gap_filled_col,
                                upper_pct_col, lower_pct_col)
        return _assign(out, rows, _CircuitEodStep.OUT_KEYS)
    rows = _run_circuit_intraday(out, p, symbol_col, date_col, prev_close_col, upper_pct_col, lower_pct_col)
    return _assign(out, rows, _CircuitIntradayStep.OUT_KEYS)


def _run_circuit_eod(out, p, symbol_col, pc_col, ex_col, gap_col, u_col, l_col) -> list[dict]:
    sym = out[symbol_col].to_numpy()
    O, H, L, C, V = (out[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close", "volume"))
    PC = _col(out, pc_col)
    EX = _col(out, ex_col, None)
    GP = _col(out, gap_col, 0)
    UB = _col(out, u_col)
    LB = _col(out, l_col)
    rows: list[dict] = []
    for a, b in _group_slices(sym):
        stp = _CircuitEodStep(p)
        for i in range(a, b):
            g = GP[i]
            rows.append(stp.step(O[i], H[i], L[i], C[i], V[i], PC[i], EX[i],
                                 bool(g) if _num(g) else False, UB[i], LB[i]))
    return rows


class _IntradaySymbolRunner:
    """Intraday circuit for one symbol: aggregates each completed session into
    a daily bar, feeds a _CircuitEodStep to obtain the band for the NEXT
    session (causal), and runs _CircuitIntradayStep per bar."""

    def __init__(self, p: CircuitParams):
        self.p = p
        self.bar_step = _CircuitIntradayStep(p)
        self.eod_step = _CircuitEodStep(p)
        self.session = None
        self.agg = None           # [o, h, l, c, v, pc_given]
        self.last_close = None    # last bar close of previous session
        self.sess_pc = None
        self.sess_band = None
        self.completed: list[tuple[Any, dict, list]] = []   # (session, eod_flags, agg) for staircase use

    def _close_session(self):
        if self.agg is None:
            return
        o, h, l, c, v, pcg = self.agg
        flags = self.eod_step.step(o, h, l, c, v, pcg, None, False, np.nan, np.nan)
        self.completed.append((self.session, flags, list(self.agg)))
        self.last_close = c

    def step(self, session, o, h, l, c, v, pc_given, ub, lb) -> dict:
        if session != self.session:
            self._close_session()
            self.session = session
            self.sess_pc = float(pc_given) if _num(pc_given) else self.last_close
            est = self.eod_step.band_state.get(None, [None])[0]
            self.sess_band = est
            self.agg = [o, h, l, c, v if _num(v) else 0.0, pc_given if _num(pc_given) else np.nan]
        else:
            a = self.agg
            if _num(h):
                a[1] = max(a[1], h) if _num(a[1]) else h
            if _num(l):
                a[2] = min(a[2], l) if _num(a[2]) else l
            if _num(c):
                a[3] = c
            if _num(v):
                a[4] += v
        if _num(ub) and _num(lb):
            bu, bl = ub, lb
        elif self.p.infer_band and self.sess_band is not None:
            bu = bl = self.sess_band
        else:
            bu = bl = np.nan
        return self.bar_step.step(session, o, h, l, c, v, self.sess_pc if self.sess_pc is not None else np.nan, bu, bl)


def _run_circuit_intraday(out, p, symbol_col, date_col, pc_col, u_col, l_col) -> list[dict]:
    sym = out[symbol_col].to_numpy()
    sess = _session_key(out[date_col]).to_numpy()
    O, H, L, C, V = (out[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close", "volume"))
    PC, UB, LB = _col(out, pc_col), _col(out, u_col), _col(out, l_col)
    rows: list[dict] = []
    for a, b in _group_slices(sym):
        run = _IntradaySymbolRunner(p)
        for i in range(a, b):
            rows.append(run.step(sess[i], O[i], H[i], L[i], C[i], V[i], PC[i], UB[i], LB[i]))
    return rows


# ============================================================================
# Flag 3 - MWCB
# ============================================================================

def _hhmm_to_minutes(s: str) -> int:
    hh, mm = s.split(":")
    return int(hh) * 60 + int(mm)


def _halt_minutes(level: int, minute_of_day: int, rules: dict) -> int | None:
    for until, mins in rules[level]:
        if minute_of_day < _hhmm_to_minutes(until):
            return mins
    return 0


def _quarter_ref_close(dates: pd.Series, closes: pd.Series) -> pd.Series:
    """For each date: last close of the PREVIOUS calendar quarter (causal)."""
    q = dates.dt.to_period("Q")
    last_by_q = closes.groupby(q).last()
    prev_q = q - 1
    return pd.Series(prev_q.map(last_by_q).to_numpy(dtype=float), index=dates.index)


def compute_mwcb_flag(
    index_df: pd.DataFrame,
    *,
    index_col: str = "index_name",
    date_col: str = "date",
    # ---- new in v2 ----
    mode: Literal["eod", "intraday"] = "eod",
    basis: Literal["prev_close", "quarterly", "auto"] = "auto",
    quarterly_basis_before: str = "2013-07-01",
    use_high_low: bool = True,
    levels: tuple[int, ...] = (10, 15, 20),
    halt_rules: dict | None = None,
    reopen_auction_minutes: int = 15,
    market_close: str = "15:30",
) -> pd.DataFrame:
    """
    Flag 3 - Market-Wide Circuit Breaker. One row per index per date (EOD) or
    per index bar (INTRADAY). Use aggregate_mwcb_by_date() before joining onto
    symbol rows (joining two indices on date alone duplicates rows).

    Required: index_name, date, close. Optional: prev_close (else derived),
    high/low (strongly recommended: the trigger is intraday -- 13-Mar-2020
    hit -10.8% at the low but closed +3.8%).

    Threshold basis: 'prev_close' (points = pct x previous close) or
    'quarterly' (points = pct x last close of the previous calendar quarter).
    'auto' uses quarterly before `quarterly_basis_before` and prev_close after.
    The Nifty history in Final_Data is consistent with this: 18-May-2009 only
    reaches 20% on the quarterly basis, 13-Mar-2020 only reaches 10% on the
    prev-close basis. VERIFY the exact cut-over date against the SEBI circular.

    EOD columns: pct_move, extreme_up_pct, extreme_dn_pct, mwcb_basis_close,
      mwcb_level (0/10/15/20), mwcb_dir ('upper'/'lower'/NaN), mwcb_triggered.
    INTRADAY columns: session_prev_close, mwcb_trigger_level (level fired on
      this bar, else 0), mwcb_level (max fired so far today), mwcb_dir,
      mwcb_halt_active (bar starts inside a halt), mwcb_reopen_auction (bar in
      the post-halt call-auction window), mwcb_halt_rest_of_day. Halt windows
      follow `halt_rules` (default: current SEBI table, SPEC 3.3).
    """
    fn = "compute_mwcb_flag"
    if mode not in ("eod", "intraday"):
        raise FlagInputError(f"{fn}: mode must be 'eod' or 'intraday', got {mode!r}.")
    if basis not in ("prev_close", "quarterly", "auto"):
        raise FlagInputError(f"{fn}: basis must be 'prev_close', 'quarterly' or 'auto'.")
    out = _prep(index_df, fn, index_col, date_col, {"close"})
    _warn_on_nulls(out, ["close"], fn)
    rules = halt_rules or DEFAULT_MWCB_HALT_RULES
    lv = sorted(levels)

    if mode == "eod":
        if "prev_close" not in out.columns:
            out["prev_close"] = out.groupby(index_col, sort=False)["close"].shift()
        if (out["prev_close"] <= 0).any():
            raise FlagInputError(f"{fn}: non-positive prev_close; cannot compute a move.")
        pc = out["prev_close"]
        hi = out["high"] if (use_high_low and "high" in out.columns) else out["close"]
        lo = out["low"] if (use_high_low and "low" in out.columns) else out["close"]
        qref = pd.concat([_quarter_ref_close(g[date_col], g["close"])
                          for _, g in out.groupby(index_col, sort=False)]).reindex(out.index)
        if basis == "prev_close":
            bref = pc
        elif basis == "quarterly":
            bref = qref.fillna(pc)
        else:
            bref = pd.Series(np.where(out[date_col] < pd.Timestamp(quarterly_basis_before), qref.fillna(pc), pc),
                             index=out.index)
        out["mwcb_basis_close"] = bref
        out["pct_move"] = (out["close"] - pc) / pc * 100
        out["extreme_up_pct"] = (hi - pc) / pc * 100
        out["extreme_dn_pct"] = (pc - lo) / pc * 100
        up_pts, dn_pts = hi - pc, pc - lo
        level = np.zeros(len(out), dtype=int)
        dirn = np.full(len(out), None, dtype=object)
        for L_ in lv:
            thr = bref * L_ / 100
            hit_up, hit_dn = (up_pts >= thr).to_numpy(), (dn_pts >= thr).to_numpy()
            hit = hit_up | hit_dn
            level = np.where(hit, L_, level)
            dirn = np.where(hit, np.where(dn_pts.to_numpy() >= up_pts.to_numpy(), "lower", "upper"), dirn)
        out["mwcb_level"] = level
        out["mwcb_dir"] = pd.Series(dirn, index=out.index).where(out["mwcb_level"] > 0)
        out["mwcb_triggered"] = out["mwcb_level"] > 0
        return out

    # ---------------- intraday ----------------
    sess = _session_key(out[date_col])
    out["_sess"] = sess
    last = out.groupby([index_col, "_sess"], sort=False)["close"].last().rename("_sc").reset_index()
    last["_prev_sc"] = last.groupby(index_col)["_sc"].shift()
    if "prev_close" in out.columns:
        spc = out.groupby([index_col, "_sess"], sort=False)["prev_close"].transform("first")
    else:
        spc = out.merge(last[[index_col, "_sess", "_prev_sc"]], on=[index_col, "_sess"], how="left")["_prev_sc"]
        spc.index = out.index
    out["session_prev_close"] = spc
    # quarterly ref from session closes
    last["_qref"] = pd.concat([_quarter_ref_close(g["_sess"], g["_sc"])
                               for _, g in last.groupby(index_col, sort=False)]).reindex(last.index)
    qmap = out.merge(last[[index_col, "_sess", "_qref"]], on=[index_col, "_sess"], how="left")["_qref"].to_numpy()
    use_q = (basis == "quarterly") | ((basis == "auto") & (out["_sess"] < pd.Timestamp(quarterly_basis_before)).to_numpy())
    bref = np.where(use_q & ~np.isnan(qmap), qmap, spc.to_numpy(dtype=float))
    hi = (out["high"] if (use_high_low and "high" in out.columns) else out["close"]).to_numpy(dtype=float)
    lo = (out["low"] if (use_high_low and "low" in out.columns) else out["close"]).to_numpy(dtype=float)
    ts = out[date_col]
    mod = (ts.dt.hour * 60 + ts.dt.minute).to_numpy()
    close_min = _hhmm_to_minutes(market_close)
    idx_arr, sess_arr, pcs = out[index_col].to_numpy(), out["_sess"].to_numpy(), spc.to_numpy(dtype=float)
    n = len(out)
    trig = np.zeros(n, dtype=int)
    lvl = np.zeros(n, dtype=int)
    dir_o = np.full(n, None, dtype=object)
    halt = np.zeros(n, dtype=bool)
    auct = np.zeros(n, dtype=bool)
    rod = np.zeros(n, dtype=bool)
    key_prev = None
    for i in range(n):
        key = (idx_arr[i], sess_arr[i])
        if key != key_prev:
            key_prev, fired, cur_lvl, cur_dir, halt_end, auct_end, rest = key, set(), 0, None, -1, -1, False
        m_ = mod[i]
        halt[i] = rest or m_ < halt_end
        auct[i] = (not halt[i]) and m_ < auct_end
        if not halt[i] and not auct[i] and _num(pcs[i]) and _num(bref[i]):
            up_pts, dn_pts = hi[i] - pcs[i], pcs[i] - lo[i]
            for L_ in lv:
                if L_ in fired:
                    continue
                thr = bref[i] * L_ / 100
                if up_pts >= thr or dn_pts >= thr:
                    fired.add(L_)
                    trig[i] = max(trig[i], L_)
                    cur_lvl = max(cur_lvl, L_)
                    cur_dir = "lower" if dn_pts >= up_pts else "upper"
            if trig[i]:
                hm = _halt_minutes(trig[i], m_, rules)
                if hm is None:
                    rest = True
                elif hm > 0:
                    halt_end = m_ + 1 + hm
                    auct_end = halt_end + reopen_auction_minutes
                    if halt_end >= close_min:
                        rest = True
        lvl[i], dir_o[i], rod[i] = cur_lvl, cur_dir, rest
    out["mwcb_trigger_level"] = trig
    out["mwcb_level"] = lvl
    out["mwcb_dir"] = pd.Series(dir_o, index=out.index).where(out["mwcb_level"] > 0)
    out["mwcb_halt_active"] = halt
    out["mwcb_reopen_auction"] = auct
    out["mwcb_halt_rest_of_day"] = rod
    out["mwcb_triggered"] = out["mwcb_level"] > 0
    return out.drop(columns=["_sess"])


def aggregate_mwcb_by_date(mwcb_df: pd.DataFrame, *, date_col: str = "date") -> pd.DataFrame:
    """Collapse per-index MWCB rows (NIFTY, SENSEX, ...) to ONE row per date
    (EOD) or per timestamp (INTRADAY): max level, its direction, any halt."""
    df = mwcb_df.sort_values(["mwcb_level"], ascending=False, kind="mergesort")
    agg = {"mwcb_level": "max", "mwcb_dir": "first", "mwcb_triggered": "any"}
    for c in ("mwcb_halt_active", "mwcb_reopen_auction", "mwcb_halt_rest_of_day"):
        if c in df.columns:
            agg[c] = "any"
    res = df.groupby(date_col, sort=True).agg(agg).reset_index()
    res["mwcb_dir"] = res["mwcb_dir"].where(res["mwcb_level"] > 0)
    return res


def apply_mwcb_windows(bars: pd.DataFrame, mwcb_intraday_agg: pd.DataFrame, *, date_col: str = "date") -> pd.DataFrame:
    """Stamp market-wide halt/auction state onto stock bars by timestamp
    (backward as-of within the same session: the most recent index bar at or
    before the stock bar). Causal."""
    b = _coerce_datetime(bars, date_col, "apply_mwcb_windows")
    b["_ord"] = np.arange(len(b))
    m = _coerce_datetime(mwcb_intraday_agg, date_col, "apply_mwcb_windows").sort_values(date_col)[[date_col, "mwcb_level", "mwcb_halt_active",
                                                "mwcb_reopen_auction", "mwcb_halt_rest_of_day"]].copy()
    m["_sess"] = m[date_col].dt.normalize()
    b["_sess"] = b[date_col].dt.normalize()
    res = pd.merge_asof(b.sort_values(date_col), m, on=date_col, by="_sess", direction="backward")
    for c in ("mwcb_halt_active", "mwcb_reopen_auction", "mwcb_halt_rest_of_day"):
        res[c] = res[c].fillna(False).astype(bool)
    res["mwcb_level"] = res["mwcb_level"].fillna(0).astype(int)
    return res.sort_values("_ord").drop(columns=["_ord", "_sess"]).reset_index(drop=True)


# ============================================================================
# Flag 4 - Staircase
# ============================================================================

def _segment_keys(out: pd.DataFrame, cols: list[str]) -> np.ndarray:
    present = [c for c in cols if c in out.columns]
    if not present:
        return np.zeros(len(out), dtype=object)
    vals = out[present].to_numpy(dtype=object)
    return np.array(["|".join(_seg_str(x) for x in row) for row in vals], dtype=object)


def _seg_str(x: Any) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ""
    return str(x)


def _ensure_circuit_cols(out: pd.DataFrame, fn: str) -> pd.DataFrame:
    """Accept v2 circuit output, or v1 output (frozen + band_hit_dir)."""
    if "circuit_locked_dir" not in out.columns:
        _require_columns(out, {"frozen", "band_hit_dir", "upside_locked"}, fn)
        out["circuit_locked_dir"] = out["band_hit_dir"].where(out["frozen"].astype(bool))
    if "data_unknown" not in out.columns:
        out["data_unknown"] = False
    if "single_price" not in out.columns:
        fr = out["frozen"].astype(bool) if "frozen" in out.columns else False
        out["single_price"] = fr & out["circuit_locked_dir"].isna()
    if "upside_locked" not in out.columns:
        out["upside_locked"] = False
    if "tick_degenerate" not in out.columns:
        out["tick_degenerate"] = False
    return out


def compute_staircase_flag(
    df: pd.DataFrame,
    params: StaircaseParams | None = None,
    *,
    symbol_col: str = "symbol",
    date_col: str = "date",
    # ---- new in v2 ----
    mode: Literal["eod", "intraday"] = "eod",
    eod_state_df: pd.DataFrame | None = None,
    segment_cols: Sequence[str] = ("exchange",),
    circuit_params: CircuitParams | None = None,
    chronic_params: ChronicParams | None = None,
) -> pd.DataFrame:
    """
    Flag 4 - Staircase. CAUSAL: a symbol is tagged from the close of the S-th
    consecutive same-direction ladder day onward; earlier days are never
    re-marked. It stays tagged (CONFIRMED/COOLING) until E consecutive NORMAL
    days (not locked, not single-price, volume >= normal_vol_frac x baseline).
    A direction flip while tagged keeps the tag (new staircase_event_id).
    UNKNOWN days (gap-filled, exchange switch) hold the state unchanged.

    mode="eod": input = compute_circuit_hit_flag(mode="eod") output (v1 output
      with frozen/band_hit_dir/upside_locked also accepted).
      Adds: day_class, vol_baseline, thin_vol, ladder_day, staircase_state,
      staircase (bool tag), staircase_dir, ladder_run_len, normal_day_count,
      staircase_event_id, staircase_early_block_dir.
      The volume baseline restarts whenever any of `segment_cols` changes
      (default: exchange -- NSE and BSE volumes differ in scale; vendor
      switches on the same exchange do not, per the full Final_Data run).

    mode="intraday": input = compute_circuit_hit_flag(mode="intraday") output.
      Each bar inherits the tag as of the PREVIOUS session's close, from
      `eod_state_df` (columns symbol, date, staircase, staircase_dir,
      staircase_state, staircase_early_block_dir[, chronic_illiquid]) or, if
      None, from a causal EOD pass over the intraday data's own completed
      sessions. Early block (params.intraday_early_block): if yesterday closed
      one ladder day short of S and every bar today so far is pinned at the
      band in that direction (>= intraday_min_pinned_bars), the bar is tagged
      provisionally. Untagging never happens intraday.
      Adds: eod_staircase_prev, eod_staircase_dir_prev, early_block,
      staircase_intraday, chronic_illiquid_prev.
    """
    fn = "compute_staircase_flag"
    p = params or StaircaseParams()
    p.validate(fn)
    if mode not in ("eod", "intraday"):
        raise FlagInputError(f"{fn}: mode must be 'eod' or 'intraday', got {mode!r}.")

    if mode == "eod":
        out = _prep(df, fn, symbol_col, date_col, {"volume"})
        out = _ensure_circuit_cols(out, fn)
        _warn_on_nulls(out, ["volume"], fn)
        seg = _segment_keys(out, list(segment_cols))
        sym = out[symbol_col].to_numpy()
        LD = out["circuit_locked_dir"].where(~out["tick_degenerate"].astype(bool)).to_numpy(dtype=object)
        UL = out["upside_locked"].to_numpy(dtype=bool)
        UN = out["data_unknown"].to_numpy(dtype=bool)
        SP = out["single_price"].to_numpy(dtype=bool)
        V = out["volume"].to_numpy(dtype=float)
        rows: list[dict] = []
        for a, b in _group_slices(sym):
            stp = _StaircaseStep(p)
            for i in range(a, b):
                ld = LD[i] if isinstance(LD[i], str) else None
                rows.append(stp.step(ld, bool(UL[i]), bool(UN[i]), bool(SP[i]), V[i], seg[i]))
        return _assign(out, rows, _StaircaseStep.OUT_KEYS)

    # ---------------- intraday propagation ----------------
    out = _prep(df, fn, symbol_col, date_col, {"pinned_run_len", "locked_since_open_dir"})
    out["_sess"] = _session_key(out[date_col])
    if eod_state_df is None:
        eod_state_df = _eod_state_from_intraday(out, p, symbol_col, date_col,
                                                circuit_params or CircuitParams(), chronic_params or ChronicParams())
    st = eod_state_df.copy()
    st = _coerce_datetime(st, date_col, fn)
    _require_columns(st, {symbol_col, date_col, "staircase", "staircase_dir", "staircase_early_block_dir"}, fn)
    st["_sess"] = st[date_col].dt.normalize()
    keep = [symbol_col, "_sess", "staircase", "staircase_dir", "staircase_early_block_dir"]
    if "chronic_illiquid" in st.columns:
        keep.append("chronic_illiquid")
    st = st[keep].sort_values("_sess")
    # previous-session state: strictly earlier session (allow_exact_matches=False)
    sess_tbl = out[[symbol_col, "_sess"]].drop_duplicates().sort_values("_sess")
    prev = pd.merge_asof(sess_tbl, st, on="_sess", by=symbol_col, direction="backward", allow_exact_matches=False)
    out = out.merge(prev, on=[symbol_col, "_sess"], how="left", suffixes=("", "_eod"))
    out["eod_staircase_prev"] = out["staircase"].fillna(False).astype(bool)
    out["eod_staircase_dir_prev"] = out["staircase_dir"]
    early_dir = out["staircase_early_block_dir"]
    minbars = (circuit_params or CircuitParams()).intraday_min_pinned_bars
    early = (p.intraday_early_block & early_dir.notna() & (out["locked_since_open_dir"] == early_dir)
             & (out["pinned_run_len"] >= minbars))
    out["early_block"] = early.fillna(False).astype(bool)
    out["staircase_intraday"] = out["eod_staircase_prev"] | out["early_block"]
    out["chronic_illiquid_prev"] = (out["chronic_illiquid"].fillna(False).astype(bool)
                                    if "chronic_illiquid" in out.columns else False)
    drop = ["_sess", "staircase", "staircase_dir", "staircase_early_block_dir"] + \
           (["chronic_illiquid"] if "chronic_illiquid" in out.columns else [])
    return out.drop(columns=drop)


def _eod_state_from_intraday(bars: pd.DataFrame, p: StaircaseParams, symbol_col: str, date_col: str,
                             cp: CircuitParams, chp: ChronicParams) -> pd.DataFrame:
    """Daily aggregates of completed intraday sessions -> causal EOD circuit,
    staircase and chronic state per session (applied to the NEXT session)."""
    g = bars.groupby([symbol_col, "_sess"], sort=True)
    daily = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                  close=("close", "last"), volume=("volume", "sum")).reset_index()
    if "prev_close" in bars.columns:
        daily["prev_close"] = g["prev_close"].first().to_numpy()
    daily = daily.rename(columns={"_sess": date_col})
    c = compute_circuit_hit_flag(daily, mode="eod", symbol_col=symbol_col, date_col=date_col, circuit_params=cp)
    s = compute_staircase_flag(c, p, symbol_col=symbol_col, date_col=date_col, segment_cols=())
    return compute_chronic_illiquidity_flag(s, chp, symbol_col=symbol_col, date_col=date_col)


# ============================================================================
# Chronic illiquidity (new)
# ============================================================================

def compute_chronic_illiquidity_flag(
    df: pd.DataFrame,
    params: ChronicParams | None = None,
    *,
    symbol_col: str = "symbol",
    date_col: str = "date",
    calendar: Sequence | pd.DatetimeIndex | None = None,
    gap_filled_col: str = "_gap_filled",
) -> pd.DataFrame:
    """
    Chronic illiquidity (EOD, causal trailing window). Input: EOD circuit
    output (needs single_price, circuit_locked_dir, volume).

    Per session: single_price (frozen, not at a band), no_trade (volume 0/NaN
    or gap-filled; plus every exchange session MISSING from the symbol's rows
    if `calendar` -- e.g. NIFTY trading dates -- is given), circuit (locked at
    a band either way). Tag when any trailing fraction >= enter_*, untag only
    when all fall below exit_* (hysteresis).
    Adds: single_price_frac, no_trade_frac, circuit_frac, chronic_obs,
    chronic_illiquid, missing_sessions.
    """
    fn = "compute_chronic_illiquidity_flag"
    p = params or ChronicParams()
    p.validate(fn)
    out = _prep(df, fn, symbol_col, date_col, {"volume"})
    out = _ensure_circuit_cols(out, fn)
    missing = np.zeros(len(out), dtype=int)
    if calendar is not None:
        cal = pd.DatetimeIndex(pd.to_datetime(pd.Index(calendar))).normalize().unique().sort_values()
        pos = cal.searchsorted(out[date_col].dt.normalize().to_numpy())
        prevpos = pd.Series(pos).groupby(out[symbol_col].to_numpy()).shift().to_numpy()
        with np.errstate(invalid="ignore"):
            mm = pos - prevpos - 1
        missing = np.where(np.isnan(mm), 0, np.clip(mm, 0, None)).astype(int)
    out["missing_sessions"] = missing
    gap = _col(out, gap_filled_col, 0)
    V = out["volume"].to_numpy(dtype=float)
    SP = out["single_price"].to_numpy(dtype=bool)
    CL = (out["circuit_locked_dir"].notna() & ~out["tick_degenerate"].astype(bool)).to_numpy()
    sym = out[symbol_col].to_numpy()
    rows: list[dict] = []
    for a, b in _group_slices(sym):
        stp = _ChronicStep(p)
        for i in range(a, b):
            g = gap[i]
            nt = (not _num(V[i])) or V[i] <= 0 or (bool(g) if _num(g) else False)
            rows.append(stp.step(bool(SP[i]) and not nt, nt, bool(CL[i]), missing[i]))
    return _assign(out, rows, _ChronicStep.OUT_KEYS)


# ============================================================================
# Exclusion table (SPEC Sec 6) + fill gates
# ============================================================================

def build_universe_exclusion_table(
    df: pd.DataFrame,
    mwcb_df: pd.DataFrame | None = None,
    *,
    mode: Literal["eod", "intraday"] = "eod",
    symbol_col: str = "symbol",
    date_col: str = "date",
    block_on_mwcb_day: bool = True,
) -> pd.DataFrame:
    """
    SPEC Sec 6 exclusion table.

    EOD input: output of circuit(eod) -> staircase(eod) -> chronic, plus
      optional mwcb_df = aggregate_mwcb_by_date(compute_mwcb_flag(...)).
    INTRADAY input: output of circuit(intraday) -> staircase(intraday), plus
      optional mwcb_df = aggregate_mwcb_by_date(compute_mwcb_flag(mode='intraday')),
      stamped by timestamp via apply_mwcb_windows.

    Columns added:
      trade_blocked   : no new buy, no new sell, no exit (owner rule). Read on
                        the DECISION bar t.
      block_reason    : '|'-joined reasons.
      exclude_from_backtest_EOD / exclude_from_backtest_INTRADAY (SPEC names).
      buy_fill_feasible_at_open / sell_fill_feasible_at_open : properties of
                        THIS bar's open for the execution SIMULATOR, read from
                        the FILL bar t+1 (a buy cannot fill at an open locked
                        at the upper band; a sell cannot at the lower band;
                        nothing fills in an MWCB halt/auction window).
    """
    fn = "build_universe_exclusion_table"
    _require_nonempty(df, fn)
    out = df.copy()
    out = _coerce_datetime(out, date_col, fn)
    reasons: list[pd.Series] = []

    def _flag(col: str) -> pd.Series:
        return out[col].fillna(False).astype(bool) if col in out.columns else pd.Series(False, index=out.index)

    if mode == "eod":
        if mwcb_df is not None:
            m = mwcb_df[[date_col, "mwcb_level", "mwcb_dir", "mwcb_triggered"]].copy()
            m[date_col] = pd.to_datetime(m[date_col]).dt.normalize()
            out = out.drop(columns=[c for c in ("mwcb_level", "mwcb_dir", "mwcb_triggered") if c in out.columns])
            out["_d"] = out[date_col].dt.normalize()
            out = out.merge(m.rename(columns={date_col: "_d"}), on="_d", how="left").drop(columns="_d")
            out["mwcb_level"] = out["mwcb_level"].fillna(0).astype(int)
            out["mwcb_triggered"] = out["mwcb_triggered"].fillna(False).astype(bool)
        stair, chron = _flag("staircase"), _flag("chronic_illiquid")
        mw = _flag("mwcb_triggered") & block_on_mwcb_day
        blocked = stair | chron | mw
        reasons = [stair.map({True: "staircase", False: ""}), chron.map({True: "chronic", False: ""}),
                   mw.map({True: "mwcb_day", False: ""})]
        halt = pd.Series(False, index=out.index)
        out["exclude_from_backtest_EOD"] = blocked
    else:
        if mwcb_df is not None:
            out = apply_mwcb_windows(out, mwcb_df, date_col=date_col)
        stair, chron = _flag("staircase_intraday"), _flag("chronic_illiquid_prev")
        halt = _flag("mwcb_halt_active") | _flag("mwcb_reopen_auction") | _flag("mwcb_halt_rest_of_day")
        blocked = stair | chron | halt
        reasons = [stair.map({True: "staircase", False: ""}), chron.map({True: "chronic", False: ""}),
                   halt.map({True: "mwcb_halt", False: ""})]
        out["exclude_from_backtest_INTRADAY"] = blocked
    out["trade_blocked"] = blocked
    out["block_reason"] = ["|".join(x for x in t if x) for t in zip(*(r.to_numpy() for r in reasons))]
    out["buy_fill_feasible_at_open"] = ~_flag("open_at_upper") & ~halt
    out["sell_fill_feasible_at_open"] = ~_flag("open_at_lower") & ~halt
    return out


# ============================================================================
# Four-column view: one column per flag
# ============================================================================

FOUR_FLAG_COLUMNS = ("PRICE_BAND", "CIRCUIT", "MWCB", "STAIRCASE")
GATE_COLUMN = "TRADE_BLOCKED"


def _band_label(u: Any, l: Any) -> str:
    def one(x):
        if not _num(x):
            return None
        if math.isinf(x):
            return "NONE"
        return f"{x:g}"
    a, b = one(u), one(l)
    if a is None and b is None:
        return "UNKNOWN"
    if a == b or b is None:
        return a if a is not None else "UNKNOWN"
    if a is None:
        return f"?/{b}"
    return f"{a}/{b}"


def four_flag_columns(df: pd.DataFrame, *, mode: Literal["eod", "intraday"] = "eod") -> pd.DataFrame:
    """
    Add one readable column per flag to the output of the flag pipeline.
    Row t is the state as of the CLOSE of bar t (bars <= t only).

    PRICE_BAND : band % in force for this session, e.g. "5", "20", "0/5"
                 (asymmetric upper/lower), "NONE" (no band, e.g. F&O when
                 supplied as inf), "UNKNOWN" (no band data and no causal
                 evidence yet).
    CIRCUIT    : EOD  - "UPPER" / "LOWER" (closed locked at the band),
                        "UPPER_1TICK" / "LOWER_1TICK" (band < 2 ticks, penny
                        stock -- informational only), "UPSIDE_LOCKED", "".
                 INTRADAY - "UPPER" / "LOWER" while the bar is inside an
                        intraday freeze (pinned >= intraday_min_pinned_bars), "".
    MWCB       : EOD  - "LOWER_10" / "UPPER_20" ... on a market-wide circuit
                        breaker day, "" otherwise.
                 INTRADAY - "HALT_<lvl>" inside a halt, "AUCTION_<lvl>" in the
                        re-opening call auction, "" otherwise; "NA" when no
                        intraday index data was supplied (EOD MWCB must never
                        be stamped on intraday bars of the same day: it is
                        only known at that day's close).
    STAIRCASE  : EOD  - "CLIMBING" / "FALLING" = tagged staircase (do not
                        trade), "BUILDING" = ladder forming (watch), "".
                 INTRADAY - "CLIMBING" / "FALLING" (tag from previous close),
                        "EARLY_BLOCK" (provisional block today), "".
    TRADE_BLOCKED : the single gate to use -- "NO" = tradable, otherwise the
                 reason(s) joined by "+": STAIRCASE, CHRONIC (chronic
                 illiquidity), MWCB_DAY (EOD) / MWCB_HALT (intraday). While
                 blocked: no new buy, no new sell, no exit; MtM continues.
                 Needs build_universe_exclusion_table() output; otherwise "NA".
    Missing inputs simply give "" (or "NA" for intraday MWCB).
    """
    out = df.copy()
    n = len(out)
    ucol = out["band_upper_used"] if "band_upper_used" in out.columns else pd.Series(np.nan, index=out.index)
    lcol = out["band_lower_used"] if "band_lower_used" in out.columns else pd.Series(np.nan, index=out.index)
    src = out["band_source"].to_numpy() if "band_source" in out.columns else np.full(n, "given", dtype=object)
    hit = out["band_hit_dir"].to_numpy() if "band_hit_dir" in out.columns else np.full(n, None)
    labels = []
    for u, l, sc, h in zip(ucol.to_numpy(), lcol.to_numpy(), src, hit):
        if sc == "given":                      # point-in-time data: may be asymmetric (GSM)
            labels.append(_band_label(u, l))
        else:                                  # inferred bands are symmetric: show the one band in force
            v = l if h == "lower" else (u if _num(u) else l)
            labels.append(_band_label(v, v))
    out["PRICE_BAND"] = labels

    if "block_reason" in out.columns:
        out[GATE_COLUMN] = [("+".join(x.upper() for x in str(r).split("|")) if isinstance(r, str) and r else "NO")
                            for r in out["block_reason"].to_numpy()]
    else:
        out[GATE_COLUMN] = "NA"

    if mode == "eod":
        ld = out["circuit_locked_dir"] if "circuit_locked_dir" in out.columns else pd.Series(None, index=out.index)
        deg = out["tick_degenerate"].fillna(False).astype(bool) if "tick_degenerate" in out.columns else pd.Series(False, index=out.index)
        ul = out["upside_locked"].fillna(False).astype(bool) if "upside_locked" in out.columns else pd.Series(False, index=out.index)
        circ = []
        for d, g, u in zip(ld.to_numpy(), deg.to_numpy(), ul.to_numpy()):
            if isinstance(d, str):
                circ.append(d.upper() + ("_1TICK" if g else ""))
            elif u:
                circ.append("UPSIDE_LOCKED")
            else:
                circ.append("")
        out["CIRCUIT"] = circ
        if "mwcb_level" in out.columns:
            lv = out["mwcb_level"].fillna(0).astype(int).to_numpy()
            dr = out["mwcb_dir"].to_numpy() if "mwcb_dir" in out.columns else np.full(n, None)
            out["MWCB"] = [f"{str(d).upper()}_{v}" if v > 0 and isinstance(d, str) else (f"LEVEL_{v}" if v > 0 else "")
                           for v, d in zip(lv, dr)]
        else:
            out["MWCB"] = ""
        stt = out["staircase_state"].to_numpy() if "staircase_state" in out.columns else np.full(n, None)
        sdr = out["staircase_dir"].to_numpy() if "staircase_dir" in out.columns else np.full(n, None)
        out["STAIRCASE"] = [(str(d).upper() if isinstance(d, str) else "TAGGED") if st in (CONFIRMED, COOLING)
                            else ("BUILDING" if st == BUILDING else "") for st, d in zip(stt, sdr)]
        return out

    # intraday
    fz = out["freeze_dir"].to_numpy() if "freeze_dir" in out.columns else np.full(n, None)
    out["CIRCUIT"] = [d.upper() if isinstance(d, str) else "" for d in fz]
    if "mwcb_halt_active" in out.columns:
        lv = out["mwcb_level"].fillna(0).astype(int).to_numpy() if "mwcb_level" in out.columns else np.zeros(n, int)
        h = (out["mwcb_halt_active"].fillna(False).astype(bool) |
             (out["mwcb_halt_rest_of_day"].fillna(False).astype(bool) if "mwcb_halt_rest_of_day" in out.columns else False)).to_numpy()
        a = out["mwcb_reopen_auction"].fillna(False).astype(bool).to_numpy() if "mwcb_reopen_auction" in out.columns else np.zeros(n, bool)
        out["MWCB"] = [f"HALT_{v}" if hh else (f"AUCTION_{v}" if aa else "") for v, hh, aa in zip(lv, h, a)]
    else:
        out["MWCB"] = "NA"
    tag = out["eod_staircase_prev"].fillna(False).astype(bool).to_numpy() if "eod_staircase_prev" in out.columns else np.zeros(n, bool)
    tdir = out["eod_staircase_dir_prev"].to_numpy() if "eod_staircase_dir_prev" in out.columns else np.full(n, None)
    eb = out["early_block"].fillna(False).astype(bool).to_numpy() if "early_block" in out.columns else np.zeros(n, bool)
    out["STAIRCASE"] = [(str(d).upper() if isinstance(d, str) else "TAGGED") if t else ("EARLY_BLOCK" if e else "")
                        for t, d, e in zip(tag, tdir, eb)]
    return out


# ============================================================================
# Streaming tracker (Paper / Live / per-bar backtests)
# ============================================================================

class CircuitLiquidityTracker:
    """
    Bar-by-bar, per-symbol tracker using the SAME step objects as the batch
    functions (identical outputs). Picklable -> persist at end of day.

    EOD:      tr.update_eod(bar)  bar keys: date, open, high, low, close, volume
                                  [, prev_close, exchange, source, _gap_filled,
                                  upper_band_pct, lower_band_pct]
              returns circuit + staircase + chronic flags for that bar.
    INTRADAY: tr.update_intraday(bar)  bar keys: date (timestamp), open, high,
              low, close, volume [, prev_close, upper_band_pct, lower_band_pct].
              Tag comes from the previous completed session (own intraday
              sessions, or seed with set_eod_state() from the EOD pipeline).
    """

    def __init__(self, symbol: str, *, circuit_params: CircuitParams | None = None,
                 staircase_params: StaircaseParams | None = None,
                 chronic_params: ChronicParams | None = None,
                 calendar: Sequence | None = None,
                 segment_cols: Sequence[str] = ("exchange",)):
        self.symbol = symbol
        self.cp = circuit_params or CircuitParams()
        self.sp = staircase_params or StaircaseParams()
        self.chp = chronic_params or ChronicParams()
        self.cp.validate("CircuitLiquidityTracker")
        self.sp.validate("CircuitLiquidityTracker")
        self.chp.validate("CircuitLiquidityTracker")
        self.segment_cols = tuple(segment_cols)
        self._cal = (pd.DatetimeIndex(pd.to_datetime(pd.Index(calendar))).normalize().unique().sort_values()
                     if calendar is not None else None)
        self._last_pos = None
        self._c = _CircuitEodStep(self.cp)
        self._s = _StaircaseStep(self.sp)
        self._k = _ChronicStep(self.chp)
        # intraday
        self._ir = _IntradaySymbolRunner(self.cp)
        self._is = _StaircaseStep(self.sp)
        self._ik = _ChronicStep(self.chp)
        self._n_done = 0
        self._eod_seed: dict | None = None
        self._last_eod: dict | None = None

    # ---------------- EOD ----------------
    def update_eod(self, bar: dict) -> dict:
        g = bar.get("_gap_filled", 0)
        gap = bool(g) if _num(g) else False
        rc = self._c.step(bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"],
                          bar.get("prev_close", np.nan), bar.get("exchange"), gap,
                          bar.get("upper_band_pct", np.nan), bar.get("lower_band_pct", np.nan))
        present = [c for c in self.segment_cols if c in bar]
        seg = "|".join(_seg_str(bar[c]) for c in present) if present else 0
        ld = None if rc["tick_degenerate"] else rc["circuit_locked_dir"]
        rs = self._s.step(ld, rc["upside_locked"], rc["data_unknown"], rc["single_price"], bar["volume"], seg)
        missing = 0
        if self._cal is not None:
            pos = int(self._cal.searchsorted(pd.Timestamp(bar["date"]).normalize()))
            if self._last_pos is not None:
                missing = max(pos - self._last_pos - 1, 0)
            self._last_pos = pos
        v = bar["volume"]
        nt = (not _num(v)) or v <= 0 or gap
        rk = self._k.step(rc["single_price"] and not nt, nt, ld is not None, missing)
        res = {**rc, **rs, **rk, "missing_sessions": missing}
        self._last_eod = res
        return res

    # ---------------- INTRADAY ----------------
    def set_eod_state(self, state: dict) -> None:
        """Seed intraday tagging with the EOD pipeline's state as of the
        previous close (keys: staircase, staircase_dir,
        staircase_early_block_dir, chronic_illiquid)."""
        self._eod_seed = dict(state)

    def update_intraday(self, bar: dict) -> dict:
        ts = pd.Timestamp(bar["date"])
        sess = ts.normalize().to_datetime64()
        r = self._ir.step(sess, bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"],
                          bar.get("prev_close", np.nan), bar.get("upper_band_pct", np.nan),
                          bar.get("lower_band_pct", np.nan))
        # feed newly completed sessions to the session-level staircase/chronic
        while self._n_done < len(self._ir.completed):
            _, fl, agg = self._ir.completed[self._n_done]
            ld = None if fl["tick_degenerate"] else fl["circuit_locked_dir"]
            rs = self._is.step(ld, fl["upside_locked"], fl["data_unknown"], fl["single_price"], agg[4], 0)
            v = agg[4]
            nt = (not _num(v)) or v <= 0
            rk = self._ik.step(fl["single_price"] and not nt, nt, ld is not None, 0)
            self._eod_seed = None  # own sessions supersede a stale seed
            self._last_eod = {**rs, **rk}
            self._n_done += 1
        st = self._eod_seed if self._eod_seed is not None else (self._last_eod or {})
        prev_tag = bool(st.get("staircase", False))
        ed = st.get("staircase_early_block_dir")
        early = bool(self.sp.intraday_early_block and ed is not None and r["locked_since_open_dir"] == ed
                     and r["pinned_run_len"] >= self.cp.intraday_min_pinned_bars)
        r.update(eod_staircase_prev=prev_tag, eod_staircase_dir_prev=st.get("staircase_dir"),
                 early_block=early, staircase_intraday=prev_tag or early,
                 chronic_illiquid_prev=bool(st.get("chronic_illiquid", False)))
        return r
