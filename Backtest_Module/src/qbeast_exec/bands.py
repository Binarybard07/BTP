r"""
qbeast_exec.bands — price bands / circuit locks (TIER 1: tradability gate).

WHAT THIS IS FOR
    An Indian listed stock has a daily price band (2/5/10/20%, or none for most F&O names). When it is
    LOCKED at the band there is only one side of the book:
        upper circuit -> buyers only, no sellers -> you CANNOT BUY
        lower circuit -> sellers only, no buyers -> you CANNOT SELL
    That asymmetry attacks a momentum strategy twice: it buys the strongest names (most likely upper-locked)
    and its crash exit sells into falling names (most likely lower-locked) - exactly when the exit matters.

WHAT THE DATA SAYS (measured on this project's universes, 1.85M symbol-days)
    REAL locks are ~0.001% of symbol-days (about 21 events in total). A naive `high==low` detector
    over-counts them by ~900-1800x because a flat daily bar is USUALLY one of:
        * an illiquid non-trade  (median return of flat bars is 0.00%)
        * tick quantisation      (JPPOWER at Rs 0.50: one Rs 0.05 tick IS a 10% "move")
    So this module will almost never fire on daily backtests. Its real job is LIVE/PAPER trading, where a
    lock does eventually happen and an order must not be assumed filled.

    => Do NOT expect this to change backtest P&L. Treat a non-zero `blocked` count as a signal to look.

DETECTION (from EOD OHLCV only - no intraday feed needed)
    band[s]   inferred from the stock's own history: a band is a HARD cap, so the robust max |return|
              identifies it, and repeated prints sitting exactly AT that value confirm it.
    locked    flat bar (high == low)  AND  |return| ~= band[s]  AND  price above `min_price`
              (the price floor removes penny-stock tick artifacts, which otherwise dominate).

MARKET-WIDE BREAKERS (10/15/20% on the index) ARE DELIBERATELY NOT MODELLED
    Measured: 8 days breached -10% in 23 years of NIFTY-100; -15% and -20% NEVER. And because these
    strategies fill at the next-day OPEN, a mid-session halt cannot change a fill that already happened -
    when a halt does occur at the open, the day's OPEN price *is* the post-halt call-auction print, so
    EOD data already embeds the outcome. Modelling the 10/15/20% x time-of-day x pre-open state machine
    would be precision that cannot move the P&L.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = ["BandConfig", "PriceBandModel", "MWCBConfig", "MarketWideBreaker"]

BUY, SELL = "BUY", "SELL"


def _side(x) -> str:
    s = str(getattr(x, "name", x)).upper()
    return BUY if s.startswith("B") or s == "1" else SELL


@dataclass
class BandConfig:
    """All inputs. Defaults suit NSE cash equity."""

    candidate_bands: tuple = (0.02, 0.05, 0.10, 0.20)
    tol: float = 0.004              # |return| within this of the band counts as "at the band"
    max_slack: float = 1.05         # robust-max may sit up to 5% under the true band
    quantile: float = 0.999         # robust max (ignores a single bad print / corporate action)
    min_history: int = 100          # fewer bars than this -> band unknown
    min_price: float = 5.0          # below this, one tick is a band-sized % -> detection meaningless
    tick: float = 0.05
    block_buy_on_upper: bool = True     # cannot buy a stock locked UP
    block_sell_on_lower: bool = True    # cannot sell a stock locked DOWN
    clamp_fills_to_band: bool = False   # TIER 2: also clamp fill prices into [prev*(1-b), prev*(1+b)]


class PriceBandModel:
    """Per-symbol band inference + a tradability gate.

    frames: {SYMBOL: DataFrame[open, high, low, close]} indexed by date.

    NOTE ON LOOKAHEAD: the band is a near-static exchange attribute, so it is inferred once from the
    symbol's full history. That is a mild, deliberate lookahead (you would know a stock's band in advance
    in real trading). Pass `causal=True` to infer only from data before each queried date instead.
    """

    def __init__(self, frames: dict[str, pd.DataFrame] | None = None,
                 config: BandConfig | None = None, causal: bool = False) -> None:
        self.cfg = config or BandConfig()
        self.frames = {str(k).upper(): v for k, v in (frames or {}).items()}
        self.causal = bool(causal)
        self._band: dict[str, float | None] = {}
        self._ret: dict[str, pd.Series] = {}
        self._flat: dict[str, pd.Series] = {}
        self._close: dict[str, pd.Series] = {}
        self.blocked: list[dict] = []          # audit trail of every refused order
        self._prepare()

    # ------------------------------------------------------------------ setup
    def _prepare(self) -> None:
        for s, d in self.frames.items():
            if d is None or len(d) < 2 or not {"high", "low", "close"}.issubset(d.columns):
                continue
            idx = pd.DatetimeIndex(d.index).normalize()
            close = pd.Series(d["close"].to_numpy(dtype=float), index=idx)
            ret = close / close.shift(1) - 1.0
            flat = pd.Series((d["high"].to_numpy(dtype=float) - d["low"].to_numpy(dtype=float)) == 0.0,
                             index=idx)
            self._close[s], self._ret[s], self._flat[s] = close, ret, flat
            self._band[s] = None if self.causal else self.infer_band(ret)

    def infer_band(self, ret: pd.Series) -> float | None:
        """The smallest candidate band that the stock's returns never exceed. None if undeterminable."""
        a = ret.abs().dropna()
        if len(a) < self.cfg.min_history:
            return None
        mx = float(a.quantile(self.cfg.quantile))
        if not np.isfinite(mx) or mx <= 0:
            return None
        for b in self.cfg.candidate_bands:
            if mx <= b * self.cfg.max_slack:
                return b
        return None                                        # exceeds every band -> no band (F&O name)

    def band(self, symbol: str, date=None) -> float | None:
        s = str(symbol).upper()
        if s not in self._ret:
            return None
        if not self.causal:
            return self._band.get(s)
        r = self._ret[s]
        if date is not None:
            r = r.loc[r.index < pd.Timestamp(date).normalize()]
        return self.infer_band(r)

    # ------------------------------------------------------------------ state
    def state(self, symbol: str, date) -> str:
        """'upper' | 'lower' | 'open' | 'unknown' for `symbol` on `date`.

        'open' = tradeable. 'unknown' = no data/band for that symbol-day (treated as tradeable)."""
        s = str(symbol).upper()
        if s not in self._ret or date is None:
            return "unknown"
        t = pd.Timestamp(date).normalize()
        try:
            flat = bool(self._flat[s].get(t))
            r = float(self._ret[s].get(t))
            px = float(self._close[s].get(t))
        except Exception:                                  # noqa: BLE001 — symbol not traded that day
            return "unknown"
        if not flat or not np.isfinite(r) or not np.isfinite(px):
            return "open"
        if px < self.cfg.min_price:                        # penny stock: one tick fakes a band move
            return "open"
        b = self.band(s, t if self.causal else None)
        if b is None:
            return "open"
        if abs(abs(r) - b) > self.cfg.tol:                 # flat but NOT at the band -> illiquid, not locked
            return "open"
        return "upper" if r > 0 else "lower"

    def can_trade(self, symbol: str, date, side) -> tuple[bool, str]:
        """(allowed, reason). A locked stock refuses only the side that cannot execute."""
        st = self.state(symbol, date)
        sd = _side(side)
        if st == "upper" and sd == BUY and self.cfg.block_buy_on_upper:
            return False, "upper circuit — buyers only, cannot BUY"
        if st == "lower" and sd == SELL and self.cfg.block_sell_on_lower:
            return False, "lower circuit — sellers only, cannot SELL"
        return True, st

    def check(self, symbol: str, date, side, qty: float = 0.0) -> bool:
        """can_trade(), but records every refusal for the report. Use this at ORDER-SUBMISSION time.

        Why submission time and not fill time: Nautilus's matching engine FALLS BACK to standard fill
        logic when a custom FillModel returns an empty book (engine.pyx: 'if not fills: return
        determine_market_price_and_volume(order)'), so a lock CANNOT be enforced by withholding liquidity.
        The order simply must not be sent."""
        ok, why = self.can_trade(symbol, date, side)
        if not ok:
            self.blocked.append(dict(symbol=str(symbol).upper(), date=pd.Timestamp(date),
                                     side=_side(side), qty=float(qty), reason=why,
                                     band=self.band(symbol)))
        return ok

    # ------------------------------------------------------------------ tier 2
    def clamp(self, symbol: str, price: float, prev_close: float, date=None) -> float:
        """TIER 2: a fill cannot print outside the band. No-op unless clamp_fills_to_band is set."""
        if not self.cfg.clamp_fills_to_band or not (prev_close > 0):
            return float(price)
        b = self.band(symbol, date)
        if b is None:
            return float(price)
        return float(np.clip(price, prev_close * (1.0 - b), prev_close * (1.0 + b)))

    # ------------------------------------------------------------------ reporting
    def bands_table(self) -> pd.DataFrame:
        rows = [dict(symbol=s, band=self.band(s),
                     bars=int(self._ret[s].notna().sum())) for s in sorted(self._ret)]
        return pd.DataFrame(rows)

    def blocked_table(self) -> pd.DataFrame:
        return pd.DataFrame(self.blocked)

    def summary(self) -> dict:
        b = self.blocked_table()
        d = dict(symbols=len(self._ret), banded=sum(1 for s in self._ret if self.band(s) is not None),
                 blocked=len(b))
        if len(b):
            d["blocked_buys"] = int((b.side == BUY).sum())
            d["blocked_sells"] = int((b.side == SELL).sum())
            d["symbols_affected"] = int(b.symbol.nunique())
            d["first"], d["last"] = b.date.min(), b.date.max()
        return d

    def scan(self) -> pd.DataFrame:
        """Every REAL lock in the loaded history — the evidence behind any claim about circuits."""
        rows = []
        for s in self._ret:
            b = self.band(s)
            if b is None:
                continue
            r, flat, px = self._ret[s], self._flat[s], self._close[s]
            hit = flat & ((r.abs() - b).abs() < self.cfg.tol) & (px >= self.cfg.min_price)
            for t in r.index[hit.fillna(False)]:
                rows.append(dict(symbol=s, date=t, ret=float(r.loc[t]), band=b,
                                 direction="upper" if r.loc[t] > 0 else "lower"))
        return pd.DataFrame(rows).sort_values("date").reset_index(drop=True) if rows else pd.DataFrame()

# ===================================================================================================
# MARKET-WIDE CIRCUIT BREAKER (MWCB)
# ===================================================================================================
# Everything above is PER SYMBOL: one stock hits its own band and only that stock stops. This is the
# other thing entirely - SEBI's index-level breaker, which halts EVERY stock and every F&O contract at
# once, liquid or not. It is not a liquidity screen; it is a market state.
#
# WHY IT IS A FLAG AND NOT A GATE
#     These strategies fill at the NEXT SESSION'S OPEN. A halt that starts and resolves inside a
#     session cannot change a fill that has not happened yet, and when a halt runs to the close the
#     next day's open is a normal pre-open auction. When a halt occurs AT the open, that day's open
#     price IS the post-halt call-auction print - so a daily bar already embeds the outcome either way.
#     Modelling the 10/15/20% x time-of-day x pre-open state machine at EOD would be precision that
#     cannot move the P&L, and pretending otherwise would be worse than saying so.
#
#     So `MarketWideBreaker` marks the session and explains it. It does NOT block orders, and enabling
#     it must not change a single number in an EOD backtest. `stress()` is where its real value is.
#
# WHY IT MUST READ NIFTY 50 (OR SENSEX), NOT YOUR BENCHMARK
#     SEBI defines the trigger on NIFTY 50 / SENSEX, whichever breaches first. Running the same test on
#     a small-cap or mid-cap index OVER-TRIGGERS, because those indices are simply more volatile - a
#     day the broad market never halted gets marked as a halt. Measured on this project's data: the
#     SmallCap-250 series breaches -10% on two sessions, one of which (2008-01-21) NIFTY 50 did not.
#     Pass the NIFTY 50 close series, whatever the book actually trades.
#
# THE POINT THAT MATTERS MOST
#     With one 10% trigger in over twenty years and zero 20% triggers ever, almost any backtest window
#     contains NO event at all. That does not mean the risk is absent - it means the stop-loss, margin
#     and reopen-gap logic for this scenario is LITERALLY UNTESTED by historical replay. `stress()`
#     injects a synthetic halt so that logic can be exercised deliberately rather than hoped about.


@dataclass
class StaircaseConfigV1:
    """Inputs for the staircase detector. Defaults tuned for NSE micro/small caps."""

    window: int = 20                # trailing sessions examined
    min_hits: int = 3               # upper-band closes inside that window to raise the flag
    min_run: int = 3                # OR this many CONSECUTIVE upper-band closes
    direction: str = "up"           # "up" (the pump pattern) | "both"
    min_price: float = 5.0          # below this one tick is a band-sized move -> meaningless
    block_buy: bool = True          # refuse BUY while flagged
    block_sell: bool = False        # selling OUT of a staircase is normally what you want to allow
    require_lock: bool = False      # also demand ZERO intraday range (high == low)

    # WHY require_lock DEFAULTS TO FALSE, measured on 888,022 micro-cap symbol-days:
    #   closed at/above the upper band      531  (0.060%)
    #   ... AND with zero intraday range     22  (0.0025%)
    # Demanding an exact lock leaves 21 episodes with a MAXIMUM RUN OF 2 -- the detector can then never
    # reach min_run and is dead code. The tradeable signature is the RETURN pattern (repeated closes at
    # the band), not a perfectly flat bar, which is an artefact of how the feed rounds. Set it True only
    # if your feed genuinely prints high == low on locked sessions.


class StaircaseFlagV1:
    """Flags symbols climbing their upper circuit in steps, and refuses to BUY them while they do.

    THE PATTERN
        A stock closes locked at its upper band, day after day, with no intraday range: a perfect
        staircase on the chart. It is the signature of an operator-driven ladder, not price discovery.
        Micro caps are where it lives, because a small book can hold the bid there.

    WHY IT MUST BLOCK BUYS SPECIFICALLY
        Momentum ranks these names top: a run of +5% locked sessions is, arithmetically, the strongest
        momentum in the universe. So the strategy will want to buy exactly the thing that cannot be
        bought (an upper lock has no sellers) and whose price is not real. The circuit gate in
        PriceBandModel already refuses the individual locked session; this refuses the NAME for as long
        as the pattern persists, which is the thing that actually protects the book.

        Selling is left open by default: if you already hold one, you want the exits available.

    CAUSAL
        Everything uses sessions up to and including the decision date. The order fills on the next
        open, so no future bar is consulted.

    USE
        sc = StaircaseFlag(frames, StaircaseConfig())
        if sc.check(sym, date, "BUY", qty):      # records every refusal
            ...place the order...
        print(sc.report(date, held=set(portfolio)))     # what is flagged, and which you hold
    """

    def __init__(self, frames: dict | None = None, config: StaircaseConfigV1 | None = None,
                 bands: "PriceBandModel | None" = None) -> None:
        self.cfg = config or StaircaseConfigV1()
        self.frames = frames or {}
        # reuse the band model: inferring the band per symbol is exactly the same problem
        self.bands = bands if bands is not None else PriceBandModel(self.frames, BandConfig())
        self.blocked: list[dict] = []
        self._hit: dict[str, pd.Series] = {}
        self._prepare()

    def _prepare(self) -> None:
        """Per symbol, a boolean series: did this session close locked at the upper band?"""
        for s, d in self.frames.items():
            s = str(s).upper()
            if d is None or len(d) < 2 or not {"high", "low", "close"}.issubset(d.columns):
                continue
            idx = pd.DatetimeIndex(d.index).normalize()
            close = pd.Series(d["close"].to_numpy(dtype=float), index=idx)
            high = pd.Series(d["high"].to_numpy(dtype=float), index=idx)
            low = pd.Series(d["low"].to_numpy(dtype=float), index=idx)
            ret = close / close.shift(1) - 1.0
            b = self.bands.band(s)
            if b is None:
                continue
            # AT OR ABOVE the band, not "within tol of it": a ladder session is one that went as far as
            # it was allowed to go, and a feed that rounds slightly past the band still counts.
            up = ret >= (b - self.bands.cfg.tol)
            hit = up if str(self.cfg.direction).lower() == "up" else (ret.abs() >= b - self.bands.cfg.tol)
            hit = hit & (close >= self.cfg.min_price)
            if self.cfg.require_lock:
                hit = hit & ((high - low) == 0.0)
            self._hit[s] = hit.fillna(False)

    # ------------------------------------------------------------------ state
    def steps(self, symbol: str, date) -> tuple[int, int]:
        """(hits in the trailing window, length of the run ending on `date`)."""
        s = str(symbol).upper()
        h = self._hit.get(s)
        if h is None or date is None:
            return 0, 0
        w = h.loc[:pd.Timestamp(date).normalize()]
        if w.empty:
            return 0, 0
        hits = int(w.tail(self.cfg.window).sum())
        run = 0
        for v in reversed(w.to_numpy()):                     # consecutive hits ending at `date`
            if not bool(v):
                break
            run += 1
        return hits, run

    def flagged(self, symbol: str, date) -> bool:
        """True while the staircase pattern is live for this symbol."""
        hits, run = self.steps(symbol, date)
        return hits >= self.cfg.min_hits or run >= self.cfg.min_run

    def can_trade(self, symbol: str, date, side) -> tuple[bool, str]:
        if not self.flagged(symbol, date):
            return True, "open"
        sd = _side(side)
        hits, run = self.steps(symbol, date)
        why = (f"staircase — {hits} upper-band closes in {self.cfg.window} sessions "
               f"(run {run}); operator ladder, price is not discovery")
        if sd == BUY and self.cfg.block_buy:
            return False, why
        if sd == SELL and self.cfg.block_sell:
            return False, why
        return True, why

    def check(self, symbol: str, date, side, qty: float = 0.0) -> bool:
        """can_trade(), recording every refusal. Call at ORDER-SUBMISSION time, like the band gate."""
        ok, why = self.can_trade(symbol, date, side)
        if not ok:
            hits, run = self.steps(symbol, date)
            self.blocked.append(dict(symbol=str(symbol).upper(), date=pd.Timestamp(date),
                                     side=_side(side), qty=float(qty), reason=why,
                                     hits=hits, run=run, band=self.bands.band(symbol)))
        return ok

    # ------------------------------------------------------------------ reporting
    def flagged_symbols(self, date) -> list[str]:
        """Every symbol flagged on `date`, worst ladder first."""
        out = []
        for s in self._hit:
            hits, run = self.steps(s, date)
            if hits >= self.cfg.min_hits or run >= self.cfg.min_run:
                out.append((hits, run, s))
        return [s for _, _, s in sorted(out, reverse=True)]

    def table(self, date, held=None) -> pd.DataFrame:
        """Flagged symbols on `date`, with the ladder measurements and whether YOU HOLD them."""
        held = {str(x).upper() for x in (held or set())}
        rows = []
        for s in self.flagged_symbols(date):
            hits, run = self.steps(s, date)
            px = self.frames.get(s)
            last = float(px["close"].iloc[-1]) if px is not None and len(px) else float("nan")
            rows.append(dict(symbol=s, hits=hits, run=run, window=self.cfg.window,
                             band=self.bands.band(s), close=last, held=s in held))
        return pd.DataFrame(rows)

    def blocked_table(self) -> pd.DataFrame:
        return pd.DataFrame(self.blocked)

    def summary(self) -> dict:
        b = self.blocked_table()
        d = dict(symbols=len(self._hit), blocked=len(b))
        if len(b):
            d["blocked_buys"] = int((b.side == BUY).sum())
            d["symbols_affected"] = int(b.symbol.nunique())
            d["first"], d["last"] = b.date.min(), b.date.max()
        return d

    def report(self, date, held=None) -> str:
        """A terminal block: what is flagged right now, and which of them you are holding."""
        tb = self.table(date, held)
        if tb.empty:
            return f"  [staircase] none flagged on {pd.Timestamp(date).date()} " \
                   f"({len(self._hit)} symbols scanned)"
        mine = tb[tb["held"]]
        lines = [f"  [staircase] {len(tb)} symbol(s) flagged on {pd.Timestamp(date).date()} "
                 f"— BUY refused while the ladder persists"]
        for _, r in tb.iterrows():
            lines.append(f"      {'HELD ' if r['held'] else '     '}{r['symbol']:<14} "
                         f"{int(r['hits'])} upper-band closes / {self.cfg.window} sessions "
                         f"(run {int(r['run'])}, band {r['band']:.0%})"
                         if r["band"] is not None else
                         f"      {'HELD ' if r['held'] else '     '}{r['symbol']:<14} "
                         f"{int(r['hits'])} hits (run {int(r['run'])})")
        if len(mine):
            lines.append(f"      -> {len(mine)} already HELD: exits stay open, further buying refused")
        return "\n".join(lines)

    def scan(self) -> pd.DataFrame:
        """Every staircase episode in the loaded history — the evidence behind any claim about them."""
        rows = []
        for s, h in self._hit.items():
            if not h.any():
                continue
            grp = (h != h.shift()).cumsum()[h]
            for _, idx in h[h].groupby(grp):
                d = idx.index
                if len(d) >= self.cfg.min_run:
                    rows.append(dict(symbol=s, start=d[0], end=d[-1], sessions=len(d),
                                     band=self.bands.band(s)))
        return pd.DataFrame(rows).sort_values("sessions", ascending=False) if rows else pd.DataFrame()


# ===================================================================================================
# THE v2.4.1 GATE - what `StaircaseFlag` now means
# ===================================================================================================
# `StaircaseFlagV1` above is kept, and only for reproducing numbers that were produced with it:
# `reconcile.py` and the archived runs exist to show that a refactor changed nothing, which they
# cannot do against different rules. Nothing else should use it.
#
# The rules that moved, and why every result built on V1 shifts:
#   V1   blocked BUYS only; `block_sell` defaulted False with the note that "selling OUT of a
#        staircase is normally what you want to allow". On a locked market there is no bid to sell
#        into, so that was never true - it just made the backtest cheaper than reality.
#   V1   looked for the pump only (`direction="up"`). A collapsing ladder is equally untradeable.
#   V1   had no untag rule: when the trailing window stopped showing enough band closes the tag
#        evaporated, with no requirement that normal trading had resumed.
#
# v2.4.1: both directions, no buy / no sell / no exit while blocked, and a tag that ends only after
# `end_confirm_days` NORMAL sessions with real volume. Plus chronic illiquidity and the market-wide
# breaker, which V1 did not have at all.

from qbeast_exec.staircase241 import (          # noqa: E402
    GATE_OK,
    Staircase241 as StaircaseFlag,
    Staircase241Config as _Cfg241,
)

# Every V1-only knob, and the v2.4.1 setting that replaced it. Refused by name: a parameter that is
# accepted and then ignored is how a risk control quietly stops working.
_SUPERSEDED = {
    "window": "the trailing window is gone - v2.4.1 tags on start_confirm_days CONSECUTIVE "
              "same-direction ladder days",
    "min_hits": "replaced by start_confirm_days (consecutive, not hits-in-a-window)",
    "min_run": "replaced by start_confirm_days",
    "direction": "v2.4.1 blocks BOTH directions - CLIMBING and FALLING - and that is not optional",
    "require_lock": "replaced by the library's day_class (locked / single-price / no-trade)",
    "block_sell": "v2.4.1 blocks buys AND sells AND exits while TRADE_BLOCKED != NO. Pass "
                  "block_sell=False only to MEASURE the rule's cost, via StaircaseConfig(_allow_"
                  "relaxed=True), and the run will label itself RELAXED",
}


def StaircaseConfig(**kw):            # noqa: N802 - it replaces a class, and keeps its name
    """The v2.4.1 staircase config, refusing V1 parameters by name.

    Returns a `Staircase241Config`. `_allow_relaxed=True` permits turning a part of the gate off for
    a deliberate A/B of what the rule costs; `summary()` then reports RELAXED so the two runs can
    never be mistaken for each other.
    """
    relaxed = bool(kw.pop("_allow_relaxed", False))
    bad = {k: why for k, why in _SUPERSEDED.items() if k in kw}
    if bad:
        if relaxed and set(bad) <= {"block_sell", "block_buy"}:
            bad = {}
        else:
            lines = "\n".join(f"    {k}={kw[k]!r}: {why}" for k, why in bad.items())
            raise TypeError(
                "StaircaseConfig: these parameters described the OLD staircase flag and have no "
                f"v2.4.1 meaning:\n{lines}\n"
                "  Set start_confirm_days / end_confirm_days / normal_vol_frac / volume_lookback "
                "instead. See qbeast_exec/staircase241.py.")
    # The SUPERSEDED names go, always - they describe a detector that no longer exists.
    for k in ("direction", "window", "min_hits", "min_run", "require_lock"):
        kw.pop(k, None)
    # block_buy / block_sell / block_exit are REAL v2.4.1 settings and must survive a relaxed call.
    # Dropping them here was a bug: `StaircaseConfig(block_sell=False, _allow_relaxed=True)` silently
    # returned the full gate, so an A/B measuring what the rule costs would have compared a run
    # against itself and reported the cost as zero.
    if not relaxed:
        for k in ("block_buy", "block_sell", "block_exit"):
            kw.pop(k, None)
    return _Cfg241(**kw)


@dataclass
class MWCBConfig:
    """SEBI's index-level breaker. Defaults are the published Indian equity rules."""

    levels: tuple = (0.10, 0.15, 0.20)
    # level -> (halt minutes before 13:00, 13:00-14:30, after 14:30). None = rest of day.
    halts: dict = field(default_factory=lambda: {
        0.10: (45, 15, 0),
        0.15: (105, 45, None),
        0.20: (None, None, None),
    })
    reopen_auction_minutes: int = 15      # every reopening is a pre-open call auction, not continuous


class MarketWideBreaker:
    """Which sessions the whole market halted, at what level, and what that implies.

    `index_close` must be the NIFTY 50 (or SENSEX) close series - see the note above.

        mw = MarketWideBreaker(nifty50_close)
        mw.scan()                 # every historical trigger
        mw.state(date)            # {'triggered', 'level', 'direction', 'pct_move', 'halt_minutes'}
        mw.stress(date, -0.10)    # a SYNTHETIC halt, for testing logic history never exercised
    """

    def __init__(self, index_close: "pd.Series", config: MWCBConfig | None = None,
                 index_name: str = "NIFTY 50") -> None:
        self.cfg = config or MWCBConfig()
        self.index_name = str(index_name)
        s = pd.Series(index_close).dropna()
        s.index = pd.DatetimeIndex(s.index).normalize()
        self._close = s.sort_index()
        self._ret = self._close / self._close.shift(1) - 1.0
        self._synthetic: dict = {}                      # date -> forced pct move, from stress()

    # ------------------------------------------------------------------ core
    def _level_of(self, move: float):
        """The most severe level this move breaches, or None. Ordered high->low so a -21% day reports
        20 and not 10 - reporting the mildest breached level would understate a halt-for-the-day."""
        if not np.isfinite(move):
            return None
        for lv in sorted(self.cfg.levels, reverse=True):
            if abs(move) >= lv:
                return lv
        return None

    def halt_minutes(self, level: float, when: str = "pre1300"):
        """Halt length in minutes for a level at a time of day. None means 'rest of the session'.

        EOD data carries no time of day, so `state()` reports the PRE-1300 duration: the longest, and the
        only honest default when the actual trigger time is unknown."""
        row = self.cfg.halts.get(level)
        if row is None:
            return None
        return {"pre1300": row[0], "mid": row[1], "post1430": row[2]}.get(when, row[0])

    def state(self, date, when: str = "pre1300") -> dict:
        """The market-wide state for one session. Every symbol inherits this - it is not per-symbol."""
        t = pd.Timestamp(date).normalize()
        move = self._synthetic.get(t)
        synthetic = move is not None
        if move is None:
            try:
                move = float(self._ret.get(t))
            except Exception:                            # noqa: BLE001 - index did not trade that day
                move = float("nan")
        lv = self._level_of(move) if np.isfinite(move) else None
        return dict(date=t, index=self.index_name, pct_move=move, triggered=lv is not None,
                    level=lv, direction=(None if lv is None else ("lower" if move < 0 else "upper")),
                    halt_minutes=(None if lv is None else self.halt_minutes(lv, when)),
                    rest_of_day=(lv is not None and self.halt_minutes(lv, when) is None),
                    synthetic=synthetic)

    def scan(self) -> "pd.DataFrame":
        """Every historical trigger. This is the evidence behind any claim about how rare these are."""
        rows = []
        for t, m in self._ret.items():
            lv = self._level_of(m) if np.isfinite(m) else None
            if lv is not None:
                rows.append(self.state(t))
        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["date", "index", "pct_move", "triggered", "level", "direction",
                     "halt_minutes", "rest_of_day", "synthetic"])

    def near_misses(self, floor: float = 0.05) -> "pd.DataFrame":
        """Sessions that moved past `floor` without breaching the mildest level. Useful context: it
        shows how far the distribution actually sits from the first trigger."""
        lo = min(self.cfg.levels)
        m = self._ret[(self._ret.abs() >= floor) & (self._ret.abs() < lo)]
        return pd.DataFrame(dict(date=m.index, pct_move=m.values)).sort_values("pct_move")

    # ------------------------------------------------------------------ stress
    def stress(self, date, pct_move: float) -> dict:
        """Inject a SYNTHETIC halt on `date`, overriding the real move.

        This exists because the historical record cannot test this path: one 10% trigger in twenty-odd
        years, and 15%/20% never. Risk logic that has never run is not 'low risk', it is untested. Use
        this to drive a deliberate rehearsal, then `clear_stress()`.

        Returns the resulting state, which is flagged `synthetic=True` so no report can mistake a
        rehearsal for history.
        """
        t = pd.Timestamp(date).normalize()
        self._synthetic[t] = float(pct_move)
        return self.state(t)

    def clear_stress(self) -> int:
        n = len(self._synthetic)
        self._synthetic.clear()
        return n

    def reopen_gap(self, date, pct_move: float, recovery: float = 0.5) -> dict:
        """What the post-halt reopen plausibly costs an order queued for that session's open.

        The reopen is a thin PRE-OPEN CALL AUCTION, not continuous two-sided trading, so the print can
        sit well away from the last continuous price - and it hits every symbol at once. `recovery` is
        the fraction of the move assumed retraced by the auction; 0.5 is a placeholder, not a measured
        figure, and is labelled as such because this project has no auction-level data to calibrate on.
        """
        st = self.state(date)
        gap = float(pct_move) * (1.0 - float(recovery))
        return dict(**st, assumed_reopen_gap=gap, recovery_assumed=float(recovery),
                    calibrated=False)

    # ------------------------------------------------------------------ reporting
    def summary(self) -> dict:
        sc = self.scan()
        d = dict(index=self.index_name, sessions=int(self._ret.notna().sum()),
                 first=self._close.index.min(), last=self._close.index.max(),
                 triggers=len(sc),
                 worst_down=float(self._ret.min()), worst_up=float(self._ret.max()))
        for lv in sorted(self.cfg.levels):
            d[f"breached_{int(lv*100)}pct"] = int((self._ret.abs() >= lv).sum())
        return d

    def report(self) -> str:
        s, sc = self.summary(), self.scan()
        L = [f"MARKET-WIDE CIRCUIT BREAKER - {self.index_name}",
             f"  sessions      : {s['sessions']:,}   {s['first'].date()} .. {s['last'].date()}",
             f"  worst session : {s['worst_down']*100:.2f}%   best: +{s['worst_up']*100:.2f}%"]
        for lv in sorted(self.cfg.levels):
            L.append(f"  breached {int(lv*100):>2}%  : {s[f'breached_{int(lv*100)}pct']}")
        if len(sc):
            L.append("  triggers:")
            for r in sc.itertuples():
                halt = "rest of day" if r.rest_of_day else f"{r.halt_minutes} min"
                L.append(f"    {pd.Timestamp(r.date).date()}  {r.pct_move*100:+.2f}%  "
                         f"level {int(r.level*100)}%  {r.direction}  halt {halt}")
        else:
            L.append("  triggers      : NONE in this window")
        L.append("  NOTE: EOD fills are at the next session's open, so this does NOT gate orders. "
                 "Its value is stress(): the risk path here is untested by historical replay.")
        return "\n".join(L)
