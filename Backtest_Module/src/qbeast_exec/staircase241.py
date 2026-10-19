r"""
qbeast_exec/staircase241.py
===========================
THE v2.4.1 TRADABILITY GATE, behind this project's existing `StaircaseFlag` interface.

`circuit_flags.py` is the QBEAST circuit & liquidity flags library v2.4.1, vendored verbatim (its own
32 install checks and 27 unit tests pass unmodified). This module is the ADAPTER: it turns the wide
per-symbol OHLCV frames this project holds into the long frame that library wants, runs its pipeline,
and answers the questions the rest of the code already asks.

    price band  ->  circuit hit  ->  staircase  ->  chronic illiquidity  ->  TRADE_BLOCKED

WHAT CHANGES AGAINST THE OLD FLAG, AND WHY ANY RESULT BUILT ON IT MOVES
-----------------------------------------------------------------------
The flag it replaces blocked BUYS ONLY and had no untag rule - once the trailing window stopped
showing enough band closes, the tag simply evaporated. Three rule changes, all of them material:

  1. BOTH DIRECTIONS BLOCK. A ladder CLIMBING and a ladder FALLING are both untradeable. The old flag
     looked only for the pump (`direction="up"`).
  2. NO BUY, NO SELL, NO EXIT while the gate is not "NO". The old flag shipped `block_sell=False` with
     the comment that "selling OUT of a staircase is normally what you want to allow". On a real
     locked market you cannot: there is no bid to sell into. Positions are still MARKED TO MARKET, so
     the P&L of being trapped is carried - which is the point.
  3. A TAG ENDS ON EVIDENCE, NOT ON A WINDOW EXPIRING. Untagging needs `end_confirm_days` NORMAL
     sessions - not locked, not single-price, and with volume at or above `normal_vol_frac` x the
     clean-session baseline. "Volumes are there" is the test.

Plus two flags the old one did not have at all: CHRONIC illiquidity (hysteresis on the single-price,
no-trade and circuit shares - it untags only when ALL THREE fall below their exit levels) and the
MARKET-WIDE circuit breaker.

So: a backtest re-run after this switch will not reproduce its old numbers, and the difference is not
noise. It is mostly the cost of being unable to exit a ladder you are holding, which the old flag
never charged. `StaircaseFlag.summary()` reports the refusals by side so the size of it is visible
rather than inferred.

WHY AN ADAPTER AND NOT A REWRITE OF THE CALL SITES
--------------------------------------------------
`StaircaseFlag(frames, cfg)` with `.check()`, `.flagged_symbols()`, `.blocked_table()` and
`.summary()` is called from nine places across the backtests and the paper books. Changing the RULES
is the job; changing the shape of nine call sites at the same time would make a rule regression and a
plumbing regression indistinguishable. The interface stays, the rules move, and the measured
difference is attributable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from qbeast_exec import circuit_flags as CF

__all__ = ["Staircase241Config", "Staircase241", "long_frame", "GATE_OK"]

GATE_OK = "NO"            # circuit_flags' TRADE_BLOCKED value meaning "tradable"

BUY, SELL = "BUY", "SELL"


def _side(side) -> str:
    s = str(side).upper()
    return BUY if s.startswith("B") else SELL


# ===================================================================================================
# config
# ===================================================================================================
@dataclass(frozen=True)
class Staircase241Config:
    """The v2.4.1 parameters this project exposes, plus how hard the gate bites.

    The SUPERSEDED knobs are deliberately absent: `window`, `min_hits`, `min_run`, `direction`,
    `require_lock` and `block_sell` described the old detector and have no meaning here. v2.4.1 tags
    on `start_confirm_days` CONSECUTIVE same-direction ladder days and unties on `end_confirm_days`
    normal days, in both directions, blocking both sides. `StaircaseConfig` refuses them by name
    rather than accepting and ignoring them - a parameter that silently stops working is how a
    configured risk control becomes decorative.
    """

    # --- staircase (v2.4.1 StaircaseParams) ---
    start_confirm_days: int = 5      # S: consecutive same-direction ladder days to tag
    end_confirm_days: int = 2        # E: consecutive NORMAL days to untag
    normal_vol_frac: float = 0.5     # a normal day needs volume >= this x the clean baseline
    volume_lookback: int = 20        # clean sessions in that baseline
    vol_frac: float | None = None    # optional thin-volume requirement FOR a ladder day; None = off
    min_baseline_obs: int | None = None

    # --- chronic illiquidity (v2.4.1 ChronicParams) ---
    chronic: bool = True             # also gate on chronic illiquidity
    chronic_window: int = 60
    chronic_min_obs: int = 20

    # --- what the gate stops ---
    # The v2.4.1 trading rule: while TRADE_BLOCKED != "NO" there is no buy, no sell and no exit.
    # Settable only to measure the cost of the rule, never to trade against it - see `relaxed`.
    block_buy: bool = True
    block_sell: bool = True
    block_exit: bool = True

    # Below this price one tick IS a band-sized move, so a "ladder" is an artefact of the tick grid.
    # Kept from the old config: it is about the instrument, not about the detector.
    min_price: float = 5.0

    def staircase_params(self) -> "CF.StaircaseParams":
        return CF.StaircaseParams(
            start_confirm_days=int(self.start_confirm_days),
            end_confirm_days=int(self.end_confirm_days),
            normal_vol_frac=float(self.normal_vol_frac),
            volume_lookback=int(self.volume_lookback),
            vol_frac=self.vol_frac,
            min_baseline_obs=self.min_baseline_obs)

    def chronic_params(self) -> "CF.ChronicParams":
        return CF.ChronicParams(window=int(self.chronic_window), min_obs=int(self.chronic_min_obs))

    @property
    def relaxed(self) -> list[str]:
        """Which parts of the v2.4.1 trading rule this config is NOT enforcing.

        Reported by `summary()` and printed by the runners, because a run with the gate half-off must
        not be comparable-looking to one with it on."""
        off = []
        if not self.block_buy:
            off.append("buys allowed while blocked")
        if not self.block_sell:
            off.append("sells allowed while blocked")
        if not self.block_exit:
            off.append("exits allowed while blocked")
        if not self.chronic:
            off.append("chronic illiquidity not gated")
        return off


# ===================================================================================================
# wide frames -> the long frame v2.4.1 wants
# ===================================================================================================
def long_frame(frames: dict, *, min_rows: int = 2) -> pd.DataFrame:
    """{SYMBOL: ohlcv DataFrame} -> one long frame with symbol/date/open/high/low/close/volume.

    This project stores OHLCV per symbol, indexed by date. The library works on a single long frame
    and groups by symbol itself, which is also what lets it share one volume baseline per
    (symbol, exchange) segment.

    `prev_close` is NOT supplied: the library derives it per symbol and knows to treat a gap as
    UNKNOWN rather than as a move. Handing it a column computed here would lose that.
    """
    parts = []
    for s, d in (frames or {}).items():
        if d is None or len(d) < min_rows:
            continue
        cols = {c.lower(): c for c in d.columns}
        if not {"high", "low", "close"}.issubset(cols):
            continue
        idx = pd.DatetimeIndex(d.index).normalize()
        p = pd.DataFrame({
            "symbol": str(s).upper(),
            "date": idx,
            "open": pd.to_numeric(d[cols["open"]], errors="coerce").to_numpy() if "open" in cols
            else pd.to_numeric(d[cols["close"]], errors="coerce").to_numpy(),
            "high": pd.to_numeric(d[cols["high"]], errors="coerce").to_numpy(),
            "low": pd.to_numeric(d[cols["low"]], errors="coerce").to_numpy(),
            "close": pd.to_numeric(d[cols["close"]], errors="coerce").to_numpy(),
            # Volume is REQUIRED by the staircase flag - the untag rule is "volumes are there". A feed
            # without it cannot untag, so 0 is wrong and NaN is honest.
            "volume": (pd.to_numeric(d[cols["volume"]], errors="coerce").to_numpy()
                       if "volume" in cols else np.nan),
        })
        parts.append(p)
    if not parts:
        return pd.DataFrame(columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    out = pd.concat(parts, ignore_index=True)
    return out.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)


# ===================================================================================================
# the gate
# ===================================================================================================
class Staircase241:
    """v2.4.1 flags over a universe's OHLCV history, answering per (symbol, date).

    Computed ONCE at construction over the whole history, then looked up - the library is causal, so
    row t uses bars <= t only and a precomputed table carries no look-ahead. That is also why this is
    affordable: the alternative is re-running the pipeline per decision.
    """

    def __init__(self, frames: dict | None = None, config: Staircase241Config | None = None,
                 *, verbose: bool = False) -> None:
        self.cfg = config or Staircase241Config()
        self.frames = frames or {}
        self.blocked: list[dict] = []
        self._gate: dict[str, pd.Series] = {}      # symbol -> TRADE_BLOCKED by date
        self._stair: dict[str, pd.Series] = {}     # symbol -> STAIRCASE label ("", CLIMBING, FALLING)
        self._dirn: dict[str, pd.Series] = {}      # symbol -> staircase_dir
        self._runlen: dict[str, pd.Series] = {}    # symbol -> ladder_run_len
        self._normal: dict[str, pd.Series] = {}    # symbol -> normal_day_count
        self._flags: pd.DataFrame = pd.DataFrame()
        self._compute(verbose=verbose)

    # ------------------------------------------------------------------ compute
    def _compute(self, *, verbose: bool = False) -> None:
        df = long_frame(self.frames)
        if df.empty:
            return
        p, c = self.cfg.staircase_params(), self.cfg.chronic_params()

        # The library's own pipeline order (flags_io.eod_state + the gate), so this computes exactly
        # what its scripts and tests compute. Not re-implemented here.
        circ = CF.compute_circuit_hit_flag(df, mode="eod")
        stair = CF.compute_staircase_flag(circ, p)
        full = CF.compute_chronic_illiquidity_flag(stair, c)
        gated = CF.build_universe_exclusion_table(full, None, mode="eod")
        out = CF.four_flag_columns(gated, mode="eod")

        # A ladder in a sub-tick-grid price is an artefact: one tick is a band-sized move. Dropping the
        # tag (rather than the row) keeps the chronic and MWCB reasons intact for those names.
        if self.cfg.min_price and self.cfg.min_price > 0:
            cheap = out["close"].to_numpy(dtype=float) < float(self.cfg.min_price)
            if cheap.any():
                out = out.copy()
                out.loc[cheap, "STAIRCASE"] = ""
                out.loc[cheap, CF.GATE_COLUMN] = out.loc[cheap, CF.GATE_COLUMN].map(
                    lambda g: "+".join([r for r in str(g).split("+") if r != "STAIRCASE"]) or GATE_OK)

        # Chronic can be excluded from the GATE while still being reported, for measuring its cost.
        if not self.cfg.chronic:
            out[CF.GATE_COLUMN] = out[CF.GATE_COLUMN].map(
                lambda g: "+".join([r for r in str(g).split("+") if r != "CHRONIC"]) or GATE_OK)

        self._flags = out
        for s, g in out.groupby("symbol", sort=False):
            idx = pd.DatetimeIndex(g["date"]).normalize()
            self._gate[str(s).upper()] = pd.Series(g[CF.GATE_COLUMN].to_numpy(), index=idx)
            self._stair[str(s).upper()] = pd.Series(g["STAIRCASE"].to_numpy(), index=idx)
            for name, col in (("_dirn", "staircase_dir"), ("_runlen", "ladder_run_len"),
                              ("_normal", "normal_day_count")):
                if col in g.columns:
                    getattr(self, name)[str(s).upper()] = pd.Series(g[col].to_numpy(), index=idx)
        if verbose:
            n_block = int((out[CF.GATE_COLUMN] != GATE_OK).sum())
            print(f"  [staircase v2.4.1] {out['symbol'].nunique()} symbol(s), {len(out):,} symbol-day(s); "
                  f"{n_block:,} blocked ({n_block / max(len(out), 1) * 100:.2f}%)", flush=True)

    # ------------------------------------------------------------------ state
    def gate(self, symbol: str, date) -> str:
        """The TRADE_BLOCKED value for this symbol on this date. "NO" = tradable.

        `.asof` semantics: a decision can land on a session this symbol did not print (a holiday for
        it, a halt, a late listing). Carrying the last known state forward is the conservative answer
        - a name that was blocked and then stopped printing has not become tradable."""
        g = self._gate.get(str(symbol).upper())
        if g is None or date is None or g.empty:
            return GATE_OK
        d = pd.Timestamp(date).normalize()
        if d in g.index:
            return str(g.loc[d])
        prior = g.index[g.index <= d]
        return str(g.loc[prior[-1]]) if len(prior) else GATE_OK

    def reasons(self, symbol: str, date) -> list[str]:
        g = self.gate(symbol, date)
        return [] if g == GATE_OK else str(g).split("+")

    def direction(self, symbol: str, date) -> str:
        """"CLIMBING" / "FALLING" / "" - which way the ladder is going."""
        s = self._stair.get(str(symbol).upper())
        if s is None or date is None or s.empty:
            return ""
        d = pd.Timestamp(date).normalize()
        if d in s.index:
            return str(s.loc[d] or "")
        prior = s.index[s.index <= d]
        return str(s.loc[prior[-1]] or "") if len(prior) else ""

    def flagged(self, symbol: str, date) -> bool:
        """True while ANY v2.4.1 reason blocks this symbol - staircase, chronic or market-wide."""
        return self.gate(symbol, date) != GATE_OK

    def steps(self, symbol: str, date) -> tuple[int, int]:
        """(ladder run length, normal-day count) - the v2.4.1 counters.

        The old flag returned (hits in a trailing window, consecutive run). The window is gone: the
        state is now a run length counting UP to `start_confirm_days` and a normal-day count counting
        up to `end_confirm_days`. Same arity so existing reporting keeps working, different meaning -
        which is why the reporting text below names them."""
        s = str(symbol).upper()
        out = []
        for store in (self._runlen, self._normal):
            ser = store.get(s)
            if ser is None or date is None or ser.empty:
                out.append(0)
                continue
            d = pd.Timestamp(date).normalize()
            prior = ser.index[ser.index <= d]
            v = ser.loc[prior[-1]] if len(prior) else 0
            out.append(int(v) if pd.notna(v) else 0)
        return out[0], out[1]

    # ------------------------------------------------------------------ the gate, per order
    def can_trade(self, symbol: str, date, side) -> tuple[bool, str]:
        reasons = self.reasons(symbol, date)
        if not reasons:
            return True, "open"
        sd = _side(side)
        run, normal = self.steps(symbol, date)
        dirn = self.direction(symbol, date) or "TAGGED"
        why = (f"{'+'.join(reasons)} — {dirn.lower()} ladder, run {run}, "
               f"{normal}/{self.cfg.end_confirm_days} normal day(s) toward untag; "
               f"no buy, no sell and no exit while blocked (v2.4.1)")
        if sd == BUY and self.cfg.block_buy:
            return False, why
        if sd == SELL and self.cfg.block_sell:
            return False, why
        return True, why

    def check(self, symbol: str, date, side, qty: float = 0.0) -> bool:
        """can_trade(), recording every refusal. Call at ORDER-SUBMISSION time."""
        ok, why = self.can_trade(symbol, date, side)
        if not ok:
            run, normal = self.steps(symbol, date)
            self.blocked.append(dict(symbol=str(symbol).upper(), date=pd.Timestamp(date),
                                     side=_side(side), qty=float(qty), reason=why,
                                     gate=self.gate(symbol, date),
                                     direction=self.direction(symbol, date),
                                     run=run, normal_days=normal))
        return ok

    def can_exit(self, symbol: str, date) -> bool:
        """Whether a HELD position may be closed. False while blocked, under the v2.4.1 rule.

        Separate from `can_trade(..., SELL)` because an exit is the case that matters most and the one
        the old flag always allowed: it is also the case where allowing it is least defensible, since a
        locked market has no bid to sell into."""
        return not (self.flagged(symbol, date) and self.cfg.block_exit)

    # ------------------------------------------------------------------ reporting
    def flagged_symbols(self, date) -> list[str]:
        """Every symbol blocked on `date`, longest ladder first."""
        rows = []
        for s in self._gate:
            if self.flagged(s, date):
                run, _ = self.steps(s, date)
                rows.append((run, s))
        return [s for _, s in sorted(rows, reverse=True)]

    def table(self, date, held=None) -> pd.DataFrame:
        held = {str(x).upper() for x in (held or set())}
        rows = []
        for s in self.flagged_symbols(date):
            run, normal = self.steps(s, date)
            px = self.frames.get(s)
            last = float(px["close"].iloc[-1]) if px is not None and len(px) else float("nan")
            rows.append(dict(symbol=s, gate=self.gate(s, date), direction=self.direction(s, date),
                             run=run, normal_days=normal, close=last, held=s in held))
        return pd.DataFrame(rows)

    def blocked_table(self) -> pd.DataFrame:
        return pd.DataFrame(self.blocked)

    def summary(self) -> dict:
        b = self.blocked_table()
        d = dict(version="2.4.1", symbols=len(self._gate), blocked=len(b),
                 rule="no buy, no sell, no exit while TRADE_BLOCKED != NO")
        if self.cfg.relaxed:
            d["RELAXED"] = self.cfg.relaxed            # a half-off gate must be visible
        if not self._flags.empty:
            g = self._flags[CF.GATE_COLUMN]
            d["symbol_days"] = int(len(g))
            d["blocked_days"] = int((g != GATE_OK).sum())
            for r in ("STAIRCASE", "CHRONIC", "MWCB_DAY"):
                n = int(g.astype(str).str.contains(r, regex=False).sum())
                if n:
                    d[f"days_{r.lower()}"] = n
            st = self._flags["STAIRCASE"].astype(str)
            for lab in ("CLIMBING", "FALLING"):
                n = int((st == lab).sum())
                if n:
                    d[f"days_{lab.lower()}"] = n
        if len(b):
            d["refused_buys"] = int((b.side == BUY).sum())
            d["refused_sells"] = int((b.side == SELL).sum())
            d["symbols_affected"] = int(b.symbol.nunique())
            d["first"], d["last"] = b.date.min(), b.date.max()
        return d

    def report(self, date, held=None) -> str:
        tb = self.table(date, held)
        if tb.empty:
            return (f"  [staircase v2.4.1] nothing blocked on {pd.Timestamp(date).date()} "
                    f"({len(self._gate)} symbols scanned)")
        mine = tb[tb["held"]]
        lines = [f"  [staircase v2.4.1] {len(tb)} symbol(s) blocked on {pd.Timestamp(date).date()} "
                 f"— no buy, no sell, no exit"]
        for _, r in tb.iterrows():
            lines.append(f"      {'HELD ' if r['held'] else '     '}{r['symbol']:<14} "
                         f"{r['gate']:<18} {str(r['direction'] or '-'):<9} run {int(r['run'])}, "
                         f"{int(r['normal_days'])}/{self.cfg.end_confirm_days} normal")
        if len(mine):
            lines.append(f"      -> {len(mine)} HELD and TRAPPED: the position is marked to market but "
                         f"cannot be exited until the gate clears")
        return "\n".join(lines)

    def episodes(self) -> pd.DataFrame:
        """One row per tagged staircase episode, per symbol - the evidence behind any claim."""
        if self._flags.empty or "staircase" not in self._flags.columns:
            return pd.DataFrame()
        rows = []
        for s, g in self._flags.groupby("symbol", sort=False):
            tag = g["staircase"].astype(bool).to_numpy()
            if not tag.any():
                continue
            ep = (tag != np.r_[False, tag[:-1]]).cumsum()
            gg = g.assign(_ep=ep)[tag]
            for _, blk in gg.groupby("_ep"):
                rows.append(dict(symbol=str(s).upper(), start=blk["date"].min(),
                                 end=blk["date"].max(), days=len(blk),
                                 dirs=",".join(sorted({str(x) for x in blk["staircase_dir"].dropna()})),
                                 max_run=int(pd.to_numeric(blk["ladder_run_len"],
                                                           errors="coerce").max() or 0)))
        return (pd.DataFrame(rows).sort_values("days", ascending=False).reset_index(drop=True)
                if rows else pd.DataFrame())

    def scan(self) -> pd.DataFrame:
        """Kept for the old interface - `episodes()` by its previous name."""
        return self.episodes()

    @property
    def flags(self) -> pd.DataFrame:
        """The full per-symbol-day flag table, for an audit trail."""
        return self._flags
