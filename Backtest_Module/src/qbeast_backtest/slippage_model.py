r"""
qbeast_backtest/slippage_model.py
------------------------------
REALISTIC, ASYMMETRIC, PER-TRADE slippage for the Nautilus execution engine.

Instead of charging slippage as a deterministic cost (engine.py / RegimeFeeModel), this moves the actual
FILL PRICE:  trade_price = tomorrow's ACTUAL open x (1 +/- A),  drawn PER TRADE, so a fill can be favorable
OR adverse ("a blessing in disguise on some days, a hit on others").

    A[s,t] = c * ATR%[s,t]                                  magnitude  (volatility surcharge)
    p[s,t] = clip(0.5 + mu[s,t] / (2*A[s,t]), 0.5, 1.0)     P(adverse) (solves A*(2p-1) = mu)
    eps    = +A with probability p, -A with probability 1-p
    fill   = actual_open * (1 + eps)   for a BUY   |   * (1 - eps)   for a SELL

Anchoring p to mu guarantees E[eps] = mu EXACTLY, so the expected drag is the microstructure estimate while
the realised fills are genuinely two-sided. p is emergent, never an invented free parameter.

mu[s,t] is UNCAPPED - an ABSOLUTE function of spread, volatility, liquidity and ORDER SIZE (no SLIP_MIN/
SLIP_MAX, no within-universe rank interpolation, which charged the most liquid smallcap the same 0.05% as
RELIANCE and never varied through time):

    mu[s,t] = max(CS_spread/2, tick/(2*price))  +  Y * vol[s,t] * sqrt(Q / ADV[s,t])
              \_____ effective half-spread _____/    \____ square-root market impact ____/

  * Corwin-Schultz (2012) estimates the effective spread from daily HIGH/LOW - no bid/ask feed needed.
    It is CLIPPED AT ONE TICK because a spread can never be tighter than the price increment; CS is known
    to return negatives (-> 0) in high volatility, i.e. the tightest spread exactly when spreads are widest.
  * The square-root impact law (Almgren; Toth-Bouchaud) adds ORDER-SIZE dependence, which is what makes a
    CAPACITY analysis possible ("what AUM can this strategy carry?").
  * PARTICIPATION GUARD: the square-root law is calibrated for small participation. Orders above
    MAX_PARTICIPATION of ADV are flagged infeasible (and the rate used is capped) rather than reporting a
    nonsense 181% slippage - the honest reading is "this trade cannot be done at this size".

COST_PCT (brokerage/STT/stamp/GST) stays on the FeeModel, so mu is NOT double-counted.

Mechanism: FillModel.get_orderbook_for_fill_simulation(...) hands the matching engine a synthetic book that
the aggressive market order fills against. VERIFIED to fire under bar execution: a constant +5% offset moved
a fill to 106.05 on a bar whose HIGH was 102, i.e. the price was genuinely overridden.
"""
from __future__ import annotations

import hashlib
from typing import Callable

import numpy as np
import pandas as pd

from nautilus_trader.backtest.models import FillModel
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.enums import BookType, OrderSide
from nautilus_trader.model.objects import Price, Quantity

_UNLIMITED = 1_000_000          # book size large enough to fully fill any of our orders
_K_CS = 3.0 - 2.0 * np.sqrt(2.0)

# ---- defaults (all overridable) ------------------------------------------------------------------
Y_IMPACT = 0.5                  # square-root law coefficient (Almgren et al.)
C_ATR = 0.10                    # A = C_ATR * ATR%  (fraction of the daily range a fill can drift)
TICK = 0.05                     # NSE price increment (INR) -> the one-tick spread floor
MAX_PARTICIPATION = 0.10        # flag orders above this fraction of ADV as infeasible
PART_CAP = 1.0                  # cap the participation rate USED in the formula (avoids absurd impacts)
CS_WINDOW = VOL_WINDOW = ADV_WINDOW = ATR_N = 21


# ============================================================== microstructure estimators
def corwin_schultz(high: pd.Series, low: pd.Series, window: int = CS_WINDOW) -> pd.Series:
    """Rolling Corwin-Schultz effective spread as a FRACTION of price, causal (shifted).

    Two-day high/low ratios separate the spread from volatility. Negative estimates (a known failure mode
    in volatile periods) are clipped to zero here and floored at one tick by the caller."""
    hl = np.log(high / low) ** 2
    beta = hl + hl.shift(-1)
    h2 = pd.concat([high, high.shift(-1)], axis=1).max(axis=1)
    l2 = pd.concat([low, low.shift(-1)], axis=1).min(axis=1)
    gamma = np.log(h2 / l2) ** 2
    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / _K_CS - np.sqrt(gamma / _K_CS)
    spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    return spread.clip(lower=0.0).rolling(window, min_periods=5).median().shift(1)


def atr_pct(d: pd.DataFrame, n: int = ATR_N) -> pd.Series:
    """Wilder ATR as a fraction of close, causal (shifted). TR already contains the overnight gap."""
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    return (tr.ewm(alpha=1.0 / n, adjust=False).mean() / d["close"]).shift(1)


# ============================================================== the calibrator
class SlippageCalibrator:
    """Per-(symbol, date) slippage terms from OHLCV. Everything is causal (shifted by one day), so a fill
    on day t uses only information available at t-1's close."""

    def __init__(self, frames: dict[str, pd.DataFrame], y_impact: float = Y_IMPACT, c_atr: float = C_ATR,
                 tick: float = TICK, max_participation: float = MAX_PARTICIPATION,
                 part_cap: float = PART_CAP, seed: int = 7) -> None:
        self.y, self.c, self.tick = float(y_impact), float(c_atr), float(tick)
        self.max_part, self.part_cap, self.seed = float(max_participation), float(part_cap), int(seed)
        self._t: dict[str, dict[str, pd.Series]] = {}
        self._open: dict[str, pd.Series] = {}
        for s, d in frames.items():
            if not {"high", "low", "close", "volume"}.issubset(d.columns) or len(d) < 60:
                continue
            self._t[s] = dict(
                cs=corwin_schultz(d["high"], d["low"]),
                vol=d["close"].pct_change().rolling(VOL_WINDOW).std().shift(1),
                adv=d["volume"].rolling(ADV_WINDOW).mean().shift(1),
                atr=atr_pct(d),
            )
            if "open" in d.columns:                                # the REFERENCE the slippage is applied to
                o = d["open"].copy()
                o.index = pd.DatetimeIndex(o.index).normalize()
                self._open[s] = o
        self.infeasible: list[dict] = []          # trades that breached the participation guard

    # ---- helpers
    @staticmethod
    def _asof(sr: pd.Series, date) -> float:
        try:
            v = sr.asof(pd.Timestamp(date))
        except Exception:                                         # noqa: BLE001
            return float("nan")
        return float(v) if v is not None and np.isfinite(v) else float("nan")

    def _uniform(self, sym: str, date, side: str) -> float:
        """Deterministic U[0,1) per (symbol, fill date, side, seed) - reproducible, stateless."""
        key = f"{sym}|{pd.Timestamp(date).date()}|{side}|{self.seed}"
        return int(hashlib.md5(key.encode()).hexdigest()[:16], 16) / float(1 << 64)

    # ---- the terms
    def terms(self, sym: str, date, qty: float, price: float) -> dict:
        """mu (expected adverse cost), A (per-trade magnitude) and p (probability the fill is adverse)."""
        t = self._t.get(sym)
        if t is None or not (price > 0):
            return dict(mu=0.0, A=0.0, p=1.0, half_spread=0.0, impact=0.0, participation=0.0, feasible=True)
        cs = self._asof(t["cs"], date)
        vol = self._asof(t["vol"], date)
        adv = self._asof(t["adv"], date)
        atr = self._asof(t["atr"], date)

        floor = self.tick / (2.0 * price)                          # a spread is never tighter than one tick
        half_spread = max(cs / 2.0, floor) if np.isfinite(cs) else floor

        participation = float(abs(qty) / adv) if (np.isfinite(adv) and adv > 0) else np.inf
        feasible = participation <= self.max_part
        used = min(participation, self.part_cap) if np.isfinite(participation) else self.part_cap
        impact = self.y * vol * np.sqrt(used) if np.isfinite(vol) else 0.0

        mu = float(half_spread + impact)
        A = float(self.c * atr) if np.isfinite(atr) else mu        # fall back to a degenerate draw
        if not (A > 0):
            A = max(mu, 1e-6)
        p = float(np.clip(0.5 + mu / (2.0 * A), 0.5, 1.0))         # solves A*(2p-1) = mu
        return dict(mu=mu, A=A, p=p, half_spread=float(half_spread), impact=float(impact),
                    participation=float(participation), feasible=bool(feasible))

    def reference(self, sym: str, date, fallback: float) -> float:
        """The price the slippage is applied to: THAT DAY'S ACTUAL OPEN.

        Critical: Nautilus's live quote at fill time is the PRIOR bar's price, so perturbing it would fold
        the overnight GAP into "slippage" - but the gap is real market movement the strategy genuinely bears,
        already captured by trading at the open. Anchoring here keeps slippage = execution cost only, and
        makes the contract literally `trade price = tomorrow's open +/- slippage`."""
        o = self._open.get(sym)
        if o is None or date is None:
            return fallback
        try:
            v = o.get(pd.Timestamp(date).normalize())
        except Exception:                                          # noqa: BLE001
            v = None
        return float(v) if v is not None and np.isfinite(v) and v > 0 else fallback

    def target_price(self, sym: str, date, side, qty: float, ref_quote: float) -> float:
        """The ABSOLUTE fill price: actual_open x (1 +/- A), two-sided with P(adverse) = p.
        BUY adverse = pay MORE (+A); SELL adverse = receive LESS (-A)."""
        ref = self.reference(sym, date, ref_quote)
        k = self.terms(sym, date, qty, ref)
        A, p = k["A"], k["p"]
        is_buy = side == OrderSide.BUY
        adverse = self._uniform(sym, date, "B" if is_buy else "S") < p
        if not k["feasible"]:
            self.infeasible.append(dict(symbol=sym, date=pd.Timestamp(date), qty=qty, price=ref,
                                        participation=k["participation"], mu=k["mu"]))
        signed = (A if adverse else -A) if is_buy else (-A if adverse else A)
        return ref * (1.0 + signed)


# ============================================================== the Nautilus FillModel
_CURRENT_DATE: pd.Timestamp | None = None


def set_current_date(ts) -> None:
    """The strategy calls this on each (index) bar so the FillModel knows which day it is filling."""
    global _CURRENT_DATE
    _CURRENT_DATE = pd.Timestamp(ts)


def current_date():
    return _CURRENT_DATE


class DynamicSlippageFillModel(FillModel):
    """Fill an aggressive market order at the ABSOLUTE price returned by `price_fn`.

    `price_fn(symbol, date, side, qty, ref_quote) -> target price`. Deliberately dumb: it only places the
    synthetic liquidity at that price; all judgement lives in the calibrator behind price_fn."""

    def __init__(self, price_fn: Callable[..., float], prob_fill_on_limit: float = 1.0,
                 random_seed: int | None = 7) -> None:
        super().__init__(prob_fill_on_limit=prob_fill_on_limit, prob_slippage=0.0, random_seed=random_seed)
        self._price_fn = price_fn

    def get_orderbook_for_fill_simulation(self, instrument, order, best_bid, best_ask):
        sym = instrument.id.symbol.value
        quote = float(best_ask) if order.side == OrderSide.BUY else float(best_bid)
        try:
            target = float(self._price_fn(sym, _CURRENT_DATE, order.side, float(order.quantity), quote))
        except Exception:                                          # noqa: BLE001 — never break a backtest
            target = quote
        if not np.isfinite(target) or target <= 0:
            target = quote
        tick = float(instrument.price_increment)
        pp, sp = instrument.price_precision, instrument.size_precision

        def _px(x: float) -> Price:
            return Price(max(tick, round(x / tick) * tick), pp)

        book = OrderBook(instrument_id=instrument.id, book_type=BookType.L2_MBP)
        if order.side == OrderSide.BUY:                            # buyer lifts the ASK
            ask = _px(target)
            book.add(BookOrder(OrderSide.SELL, ask, Quantity(_UNLIMITED, sp), 2), 0, 0)
            book.add(BookOrder(OrderSide.BUY, _px(float(ask) * 0.5), Quantity(_UNLIMITED, sp), 1), 0, 0)
        else:                                                      # seller hits the BID
            bid = _px(target)
            book.add(BookOrder(OrderSide.BUY, bid, Quantity(_UNLIMITED, sp), 1), 0, 0)
            book.add(BookOrder(OrderSide.SELL, _px(float(bid) * 1.5), Quantity(_UNLIMITED, sp), 2), 0, 0)
        return book


def constant_offset(frac: float) -> Callable[..., float]:
    """A trivial price_fn for VERIFICATION: buys fill `frac` ABOVE the quote, sells `frac` below."""
    def fn(symbol, date, side, qty, ref_quote) -> float:
        return ref_quote * (1.0 + (frac if side == OrderSide.BUY else -frac))
    return fn


# ============================================================== data + capacity
def load_ohlcv_frames(symbols) -> dict[str, pd.DataFrame]:
    """OHLCV per symbol from the configured clean source (needs HIGH/LOW, which backtest.load_frames drops)."""
    from qbeast_backtest import data_io
    out = {}
    for s in symbols:
        try:
            f = data_io.load_ohlcv(s)
        except Exception:                                          # noqa: BLE001
            continue
        c = {x.lower(): x for x in f.columns}
        if not all(k in c for k in ("high", "low", "close", "volume")):
            continue
        out[s.upper()] = pd.DataFrame({k: f[c[k]] for k in ("open", "high", "low", "close", "volume")
                                       if k in c}).dropna()
    return out


def capacity_table(cal: SlippageCalibrator, frames: dict, date, portfolios, top_n: int = 20) -> pd.DataFrame:
    """Expected mu per name at a range of PORTFOLIO sizes -> the strategy's capacity curve."""
    rows = []
    for s, d in frames.items():
        sub = d.loc[:pd.Timestamp(date)]
        if sub.empty:
            continue
        px = float(sub["close"].iloc[-1])
        if not (px > 0):
            continue
        r = {"symbol": s}
        for pf in portfolios:
            k = cal.terms(s, date, (pf / top_n) / px, px)
            r[f"{pf/1e7:,.0f}cr"] = round(k["mu"] * 100, 4)
            r[f"feas_{pf/1e7:,.0f}cr"] = k["feasible"]
        rows.append(r)
    return pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame()
