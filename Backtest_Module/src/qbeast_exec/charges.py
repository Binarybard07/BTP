r"""
qbeast_exec.charges — Indian transaction charges + slippage (shared, strategy-agnostic).

TWO things, deliberately kept apart because they behave differently:

  1. CHARGES  — deterministic statutory/broker costs (STT, exchange, SEBI, stamp, GST, brokerage, DP).
                Always a COST: cash out, never a price change. -> `ChargesCalculator`, `NautilusFeeModel`.
  2. SLIPPAGE — execution uncertainty: the price you actually get vs the price you benchmarked against.
                Can be modelled as a cost OR as a genuine PRICE move (two-sided: some fills come in
                better than the reference). -> `SlippageModel`, `NautilusFillModel`.

Charges are ITEMISED, not a single blended constant, so a strategy can see exactly what it pays and
switch segment (delivery / intraday / futures / options) without re-deriving anything.

Nautilus integration
--------------------
    engine.add_venue(..., fee_model=NautilusFeeModel(ChargesConfig.equity_delivery()),
                          fill_model=NautilusFillModel(SlippageModel(frames)))

`NautilusFeeModel.get_commission()` is what Nautilus calls on every fill; the returned Money is deducted
from the account automatically. `NautilusFillModel.get_orderbook_for_fill_simulation()` hands the matching
engine a synthetic book so the FILL PRICE lands where the slippage model says it should.

IMPORTANT: if you enable price-based slippage (NautilusFillModel), do NOT also add a slippage term to the
fee model, or you count it twice.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

__all__ = ["ChargesConfig", "ChargesCalculator", "NautilusFeeModel",
           "SlippageConfig", "SlippageModel", "NautilusFillModel"]

BUY, SELL = "BUY", "SELL"


def _side(x) -> str:
    """Normalise a side from a string or a Nautilus OrderSide enum."""
    s = str(getattr(x, "name", x)).upper()
    return BUY if s.startswith("B") or s == "1" else SELL


# ============================================================== 1. CHARGES
@dataclass
class ChargesConfig:
    """Itemised charge schedule. Rates are FRACTIONS of turnover unless named `_flat`.

    Defaults = NSE equity DELIVERY with a zero-brokerage discount broker. Use the classmethods for other
    segments. Every field is overridable, so a teammate on a different broker just edits what differs."""

    name: str = "equity_delivery"
    brokerage_pct: float = 0.0           # fraction of turnover
    brokerage_flat_max: float = 0.0      # per-order cap in currency (0 = no cap applied)
    stt_buy: float = 0.001               # 0.10% delivery
    stt_sell: float = 0.001
    exchange_txn: float = 0.0000297      # NSE cash 0.00297%
    sebi_fee: float = 0.000001           # Rs 10 per crore
    stamp_duty_buy: float = 0.00015      # 0.015%, BUY side only
    stamp_duty_sell: float = 0.0
    gst_rate: float = 0.18               # on (brokerage + exchange + sebi)
    dp_charge_flat_sell: float = 0.0     # per SELL per scrip, in currency (e.g. 15.0). Not a %.
    currency: str = "INR"

    @classmethod
    def equity_delivery(cls, **kw) -> "ChargesConfig":
        return cls(name="equity_delivery", **kw)

    @classmethod
    def equity_intraday(cls, **kw) -> "ChargesConfig":
        """STT 0.025% on the SELL side only; stamp 0.003% buy; no DP charge."""
        d = dict(name="equity_intraday", stt_buy=0.0, stt_sell=0.00025,
                 stamp_duty_buy=0.00003, dp_charge_flat_sell=0.0)
        d.update(kw)
        return cls(**d)

    @classmethod
    def futures(cls, **kw) -> "ChargesConfig":
        """Equity futures: STT 0.02% sell, exchange ~0.0019%, stamp 0.002% buy."""
        d = dict(name="futures", stt_buy=0.0, stt_sell=0.0002, exchange_txn=0.0000190,
                 stamp_duty_buy=0.00002, dp_charge_flat_sell=0.0)
        d.update(kw)
        return cls(**d)

    @classmethod
    def options(cls, **kw) -> "ChargesConfig":
        """Equity options (on PREMIUM turnover): STT 0.1% sell, exchange ~0.05%, stamp 0.003% buy."""
        d = dict(name="options", stt_buy=0.0, stt_sell=0.001, exchange_txn=0.0005,
                 stamp_duty_buy=0.00003, dp_charge_flat_sell=0.0)
        d.update(kw)
        return cls(**d)

    @classmethod
    def blended(cls, rate: float, **kw) -> "ChargesConfig":
        """A single blended rate per side (back-compat with `COST_PCT`-style models). Everything else 0."""
        d = dict(name=f"blended_{rate}", brokerage_pct=rate, stt_buy=0.0, stt_sell=0.0,
                 exchange_txn=0.0, sebi_fee=0.0, stamp_duty_buy=0.0, gst_rate=0.0)
        d.update(kw)
        return cls(**d)


class ChargesCalculator:
    """Compute itemised charges for a trade. Pure arithmetic — no state, no I/O."""

    def __init__(self, config: ChargesConfig | None = None) -> None:
        self.cfg = config or ChargesConfig()

    def breakdown(self, side, qty: float, price: float) -> dict[str, float]:
        """Every component in currency units, plus `total` and the effective `rate` of turnover."""
        c = self.cfg
        s = _side(side)
        turnover = abs(float(qty)) * float(price)

        brokerage = turnover * c.brokerage_pct
        if c.brokerage_flat_max > 0:
            brokerage = min(brokerage, c.brokerage_flat_max)
        stt = turnover * (c.stt_buy if s == BUY else c.stt_sell)
        exchange = turnover * c.exchange_txn
        sebi = turnover * c.sebi_fee
        stamp = turnover * (c.stamp_duty_buy if s == BUY else c.stamp_duty_sell)
        gst = (brokerage + exchange + sebi) * c.gst_rate
        dp = c.dp_charge_flat_sell if s == SELL else 0.0
        total = brokerage + stt + exchange + sebi + stamp + gst + dp
        return dict(side=s, turnover=turnover, brokerage=brokerage, stt=stt, exchange=exchange,
                    sebi=sebi, stamp_duty=stamp, gst=gst, dp_charge=dp, total=total,
                    rate=(total / turnover if turnover > 0 else 0.0))

    def total(self, side, qty: float, price: float) -> float:
        """Just the currency amount."""
        return self.breakdown(side, qty, price)["total"]

    def effective_rate(self, side, price: float = 1000.0, qty: float = 1000.0) -> float:
        """Effective rate of turnover for a representative trade (DP charges make it size-dependent)."""
        return self.breakdown(side, qty, price)["rate"]

    def schedule(self, price: float = 1000.0, qty: float = 1000.0) -> pd.DataFrame:
        """Side-by-side BUY/SELL breakdown — handy for documenting what a strategy actually pays."""
        rows = {s: self.breakdown(s, qty, price) for s in (BUY, SELL)}
        return pd.DataFrame(rows).drop(index=["side"], errors="ignore")


_FEE_CLS = None


def _fee_class():
    """Build (once) a real subclass of Nautilus's FeeModel. Deferred so `import qbeast_exec` works
    without Nautilus installed — Nautilus's Cython types cannot be re-based after class creation."""
    global _FEE_CLS
    if _FEE_CLS is None:
        from nautilus_trader.backtest.models import FeeModel as _Base
        from nautilus_trader.model.objects import Money

        class _QbeastFeeModel(_Base):
            def __init__(self, config, extra_rate):
                super().__init__()
                self.calc = ChargesCalculator(config)
                self.extra = extra_rate

            def get_commission(self, order, fill_qty, fill_px, instrument):
                qty, px = float(fill_qty), float(fill_px)
                amt = self.calc.total(order.side, qty, px)
                e = (self.extra.get(instrument.id.symbol.value, 0.0)
                     if isinstance(self.extra, dict) else float(self.extra))
                return Money(amt + qty * px * e, instrument.quote_currency)

        _FEE_CLS = _QbeastFeeModel
    return _FEE_CLS


def NautilusFeeModel(config: ChargesConfig | None = None,
                     extra_rate: float | dict[str, float] = 0.0):
    """A Nautilus `FeeModel` charging the itemised schedule. Pass to `engine.add_venue(fee_model=...)`.

    config     : ChargesConfig (default = NSE equity delivery, zero brokerage).
    extra_rate : optional flat float or {symbol: rate} added on top — use ONLY for COST-based slippage.
                 Leave 0.0 when using NautilusFillModel, or slippage is counted twice.
    """
    return _fee_class()(config, extra_rate)


# ============================================================== 2. SLIPPAGE
@dataclass
class SlippageConfig:
    r"""Slippage parameters.

    mode:
      "none"            no slippage.
      "fixed"           a flat rate (`fixed_rate`), always adverse.
      "rank"            legacy per-symbol rate interpolated by liquidity RANK, `rank_min`..`rank_max`.
                        (Rank-based: universe-relative and static. Kept for back-compat only.)
      "dynamic"         RECOMMENDED. mu = max(Corwin-Schultz/2, tick/2P) + Y*vol*sqrt(Q/ADV),
                        A = c*ATR%, P(adverse) = clip(0.5 + mu/(2A), 0.5, 1). Two-sided per trade,
                        uncapped, size-aware. Needs OHLCV frames.

    The dynamic model draws eps = +A (adverse) with probability p, else -A, so E[eps] = mu EXACTLY.
    Because it is anchored to mu, `p` is emergent -- never an invented free parameter.
    """

    mode: str = "dynamic"
    fixed_rate: float = 0.0005
    rank_min: float = 0.0005
    rank_max: float = 0.0025
    # dynamic model
    y_impact: float = 0.5                # square-root impact coefficient (Almgren)
    c_atr: float = 0.10                  # A = c_atr * ATR%
    tick: float = 0.05                   # min price increment -> the one-tick spread floor
    max_participation: float = 0.10      # flag fills above this fraction of ADV as infeasible
    participation_cap: float = 1.0       # cap the rate USED in the formula (avoids absurd impacts)
    cs_window: int = 21
    vol_window: int = 21
    adv_window: int = 21
    atr_n: int = 21
    seed: int = 7


def corwin_schultz(high: pd.Series, low: pd.Series, window: int = 21) -> pd.Series:
    """Rolling Corwin-Schultz (2012) effective spread as a FRACTION of price, causal (shifted).
    Estimates the bid-ask spread from daily HIGH/LOW alone -- no quote feed needed. Negative estimates
    (a known failure mode in volatile periods) clip to 0; callers should floor at one tick."""
    k = 3.0 - 2.0 * np.sqrt(2.0)
    hl = np.log(high / low) ** 2
    beta = hl + hl.shift(-1)
    h2 = pd.concat([high, high.shift(-1)], axis=1).max(axis=1)
    l2 = pd.concat([low, low.shift(-1)], axis=1).min(axis=1)
    gamma = np.log(h2 / l2) ** 2
    alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    return spread.clip(lower=0.0).rolling(window, min_periods=5).median().shift(1)


def atr_pct(d: pd.DataFrame, n: int = 21) -> pd.Series:
    """Wilder ATR as a fraction of close, causal. True Range already contains the overnight gap."""
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    return (tr.ewm(alpha=1.0 / n, adjust=False).mean() / d["close"]).shift(1)


class SlippageModel:
    """Per-(symbol, date, size) slippage. Everything causal: a fill on day t uses data up to t-1.

    frames: {SYMBOL: DataFrame[open, high, low, close, volume]} indexed by date. Only "dynamic" mode
    needs high/low/volume; "fixed"/"rank"/"none" work with an empty dict."""

    def __init__(self, frames: dict[str, pd.DataFrame] | None = None,
                 config: SlippageConfig | None = None) -> None:
        self.cfg = config or SlippageConfig()
        self.frames = frames or {}
        self._t: dict[str, dict[str, pd.Series]] = {}
        self._open: dict[str, pd.Series] = {}
        self._rank: dict[str, float] = {}
        self.infeasible: list[dict] = []
        self._records: list[dict] = []
        self._prepare()

    def _prepare(self) -> None:
        c = self.cfg
        turn = {}
        min_hist = max(c.cs_window, c.vol_window, c.adv_window, c.atr_n) + 5
        for s, d in self.frames.items():
            s = s.upper()
            if d is None or len(d) == 0:
                continue
            vol = d["volume"] if "volume" in d.columns else pd.Series(0.0, index=d.index)
            # The OPEN map and the liquidity rank are ALWAYS built — they need no warmup, and the fill
            # reference must resolve even for a short frame or a non-dynamic mode. (Gating these behind
            # the rolling-window warmup made reference_price() fall back to the live quote, which would
            # silently fold the overnight gap back into "slippage".)
            if "open" in d.columns:
                o = d["open"].copy()
                o.index = pd.DatetimeIndex(o.index).normalize()
                self._open[s] = o
            if "close" in d.columns:
                tv = (d["close"] * vol).replace([np.inf, -np.inf], np.nan).dropna()
                turn[s] = float(tv.median()) if len(tv) else 0.0
            # the rolling estimators DO need warmup; skip only these when history is too short
            if not {"high", "low", "close"}.issubset(d.columns) or len(d) < min_hist:
                continue
            self._t[s] = dict(
                cs=corwin_schultz(d["high"], d["low"], c.cs_window),
                vol=d["close"].pct_change().rolling(c.vol_window).std().shift(1),
                adv=vol.rolling(c.adv_window).mean().shift(1),
                atr=atr_pct(d, c.atr_n))
        if turn:                                                  # liquidity rank (legacy "rank" mode)
            order = sorted(turn, key=lambda x: -turn[x])
            n = max(len(order) - 1, 1)
            self._rank = {s: i / n for i, s in enumerate(order)}

    @staticmethod
    def _asof(sr: pd.Series, date) -> float:
        try:
            v = sr.asof(pd.Timestamp(date))
        except Exception:                                          # noqa: BLE001
            return float("nan")
        return float(v) if v is not None and np.isfinite(v) else float("nan")

    def _uniform(self, sym: str, date, side: str) -> float:
        """Deterministic U[0,1) per (symbol, date, side, seed): reproducible and stateless."""
        key = f"{sym}|{pd.Timestamp(date).date()}|{side}|{self.cfg.seed}"
        return int(hashlib.md5(key.encode()).hexdigest()[:16], 16) / float(1 << 64)

    def terms(self, symbol: str, date, qty: float = 0.0, price: float = 0.0) -> dict:
        """The slippage terms for one prospective fill.

        Returns mu (expected adverse cost, a FRACTION), A (per-trade magnitude), p (probability the fill
        is adverse), plus the components and a feasibility flag."""
        c = self.cfg
        sym = symbol.upper()
        base = dict(mu=0.0, A=0.0, p=1.0, half_spread=0.0, impact=0.0,
                    participation=0.0, feasible=True, mode=c.mode)
        if c.mode == "none":
            return base
        if c.mode == "fixed":
            return {**base, "mu": c.fixed_rate, "A": c.fixed_rate}
        if c.mode == "rank":
            r = self._rank.get(sym, 1.0)
            mu = c.rank_min + (c.rank_max - c.rank_min) * r
            return {**base, "mu": mu, "A": mu}

        t = self._t.get(sym)
        if t is None or not (price > 0):
            return base
        cs, vol = self._asof(t["cs"], date), self._asof(t["vol"], date)
        adv, atr = self._asof(t["adv"], date), self._asof(t["atr"], date)
        floor = c.tick / (2.0 * price)                             # a spread is never tighter than a tick
        half_spread = max(cs / 2.0, floor) if np.isfinite(cs) else floor
        part = float(abs(qty) / adv) if (np.isfinite(adv) and adv > 0 and qty) else 0.0
        feasible = part <= c.max_participation
        used = min(part, c.participation_cap)
        impact = c.y_impact * vol * np.sqrt(used) if (np.isfinite(vol) and used > 0) else 0.0
        mu = float(half_spread + impact)
        A = float(c.c_atr * atr) if np.isfinite(atr) else mu
        if not (A > 0):
            A = max(mu, 1e-6)
        p = float(np.clip(0.5 + mu / (2.0 * A), 0.5, 1.0))         # solves A(2p-1) = mu
        return dict(mu=mu, A=A, p=p, half_spread=float(half_spread), impact=float(impact),
                    participation=part, feasible=bool(feasible), mode=c.mode)

    def draw(self, symbol: str, date, side, qty: float = 0.0, price: float = 0.0) -> float:
        r"""SIGNED slippage fraction for this fill, from the side's point of view:
        positive = ADVERSE (buy pays more / sell receives less). Two-sided in "dynamic" mode."""
        k = self.terms(symbol, date, qty, price)
        s = _side(side)
        if k["A"] <= 0:
            return 0.0
        if self.cfg.mode in ("fixed", "rank"):
            eps = k["mu"]                                          # deterministic, always adverse
        else:
            adverse = self._uniform(symbol.upper(), date, s[0]) < k["p"]
            eps = k["A"] if adverse else -k["A"]
        if not k["feasible"]:
            self.infeasible.append(dict(symbol=symbol.upper(), date=pd.Timestamp(date), qty=qty,
                                        price=price, participation=k["participation"]))
        return float(eps)

    def fill_price(self, symbol: str, date, side, qty: float, reference: float) -> float:
        """The realised fill price: reference x (1 + eps) for a BUY, x (1 - eps) for a SELL.
        `reference` is the price the slippage is measured against (normally the fill bar's OPEN)."""
        eps = self.draw(symbol, date, side, qty, reference)
        px = reference * (1.0 + eps) if _side(side) == BUY else reference * (1.0 - eps)
        self._records.append(dict(symbol=symbol.upper(), date=pd.Timestamp(date), side=_side(side),
                                  qty=qty, reference=reference, fill=px, eps=eps))
        return float(px)

    def reference_price(self, symbol: str, date, fallback: float) -> float:
        """That day's OPEN, if known. Anchoring to the open keeps slippage = execution cost only; anchoring
        to the prior close would fold the overnight GAP (real market movement) into "slippage"."""
        o = self._open.get(symbol.upper())
        if o is None or date is None:
            return fallback
        try:
            v = o.get(pd.Timestamp(date).normalize())
        except Exception:                                          # noqa: BLE001
            v = None
        return float(v) if v is not None and np.isfinite(v) and v > 0 else fallback

    def realised(self) -> pd.DataFrame:
        """Every fill this model priced, with its realised eps. Use for the slippage report."""
        return pd.DataFrame(self._records)

    def summary(self) -> dict:
        """Realised adverse rate, mean drag and dispersion — the diagnostics that matter."""
        df = self.realised()
        if df.empty:
            return {}
        return dict(fills=len(df), adverse_pct=float((df.eps > 0).mean() * 100),
                    mean_eps_pct=float(df.eps.mean() * 100), median_eps_pct=float(df.eps.median() * 100),
                    p5_pct=float(df.eps.quantile(0.05) * 100), p95_pct=float(df.eps.quantile(0.95) * 100),
                    worst_pct=float(df.eps.max() * 100), best_pct=float(df.eps.min() * 100),
                    infeasible=len(self.infeasible))

    def capacity(self, symbols, date, portfolios, top_n: int = 20,
                 prices: dict | None = None) -> pd.DataFrame:
        """Expected mu per portfolio size — the strategy's capacity curve. Only meaningful in "dynamic"
        mode, because that is the only one with an order-size term."""
        rows = []
        for pf in portfolios:
            mus, bad = [], 0
            for s in symbols:
                s = s.upper()
                px = (prices or {}).get(s)
                if px is None:
                    d = self.frames.get(s)
                    if d is None or d.empty:
                        continue
                    sub = d.loc[:pd.Timestamp(date)]
                    if sub.empty:
                        continue
                    px = float(sub["close"].iloc[-1])
                if not px or px <= 0:
                    continue
                k = self.terms(s, date, (pf / top_n) / px, px)
                mus.append(k["mu"])
                bad += (not k["feasible"])
            if mus:
                a = np.array(mus)
                rows.append(dict(portfolio=pf, position=pf / top_n, median_mu_pct=float(np.median(a) * 100),
                                 mean_mu_pct=float(a.mean() * 100), worst_mu_pct=float(a.max() * 100),
                                 untradeable=bad, n=len(a)))
        return pd.DataFrame(rows)


_FILL_CLS = None


def _fill_class():
    """Build (once) a real subclass of Nautilus's FillModel (see `_fee_class` for why this is deferred)."""
    global _FILL_CLS
    if _FILL_CLS is None:
        from nautilus_trader.backtest.models import FillModel as _Base
        from nautilus_trader.model.book import OrderBook
        from nautilus_trader.model.data import BookOrder
        from nautilus_trader.model.enums import BookType, OrderSide
        from nautilus_trader.model.objects import Price, Quantity

        UNLIM = 1_000_000

        class _QbeastFillModel(_Base):
            def __init__(self, slippage, prob_fill_on_limit=1.0, random_seed=7):
                super().__init__(prob_fill_on_limit=prob_fill_on_limit, prob_slippage=0.0,
                                 random_seed=random_seed)
                self.slippage = slippage
                self._date = None

            def set_date(self, ts):
                self._date = pd.Timestamp(ts)

            def get_orderbook_for_fill_simulation(self, instrument, order, best_bid, best_ask):
                is_buy = order.side == OrderSide.BUY
                quote = float(best_ask) if is_buy else float(best_bid)
                try:
                    ref = self.slippage.reference_price(instrument.id.symbol.value, self._date, quote)
                    target = self.slippage.fill_price(instrument.id.symbol.value, self._date,
                                                      BUY if is_buy else SELL,
                                                      float(order.quantity), ref)
                except Exception:                                  # noqa: BLE001 — never break a backtest
                    target = quote
                if not np.isfinite(target) or target <= 0:
                    target = quote
                tick = float(instrument.price_increment)
                pp, sp = instrument.price_precision, instrument.size_precision

                def _px(x):
                    return Price(max(tick, round(x / tick) * tick), pp)

                book = OrderBook(instrument_id=instrument.id, book_type=BookType.L2_MBP)
                if is_buy:
                    ask = _px(target)
                    book.add(BookOrder(OrderSide.SELL, ask, Quantity(UNLIM, sp), 2), 0, 0)
                    book.add(BookOrder(OrderSide.BUY, _px(float(ask) * 0.5), Quantity(UNLIM, sp), 1), 0, 0)
                else:
                    bid = _px(target)
                    book.add(BookOrder(OrderSide.BUY, bid, Quantity(UNLIM, sp), 1), 0, 0)
                    book.add(BookOrder(OrderSide.SELL, _px(float(bid) * 1.5), Quantity(UNLIM, sp), 2), 0, 0)
                return book

        _FILL_CLS = _QbeastFillModel
    return _FILL_CLS


def NautilusFillModel(slippage: SlippageModel, prob_fill_on_limit: float = 1.0,
                      random_seed: int | None = 7):
    """A Nautilus `FillModel` that moves the FILL PRICE per a `SlippageModel`.

    Nautilus's built-in `prob_slippage` is a symbol-blind one-tick coin flip; this replaces it with a
    per-symbol, per-day, size-aware, two-sided model. Pass to `engine.add_venue(fill_model=...)`, and
    call `.set_date(bar.ts_event)` once per bar so it knows which day it is filling."""
    return _fill_class()(slippage, prob_fill_on_limit, random_seed)
