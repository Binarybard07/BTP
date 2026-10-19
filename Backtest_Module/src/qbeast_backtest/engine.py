"""
qbeast_backtest/engine.py
----------------------
The execution ledger. Integer share positions, FIFO capital-gains tax (per financial year), a daily
interest accrual on idle cash, and next-day-open fills. Every rupee of commission, slippage and tax
hits the cash balance - so the equity curve is genuinely net of everything. This is a transparent
cash-account simulation (no shorting, no leverage), the way delivery-based Indian equity actually works.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Only the two settings this module still applies itself: the cash accrual, and the fallback slippage
# rate for a symbol missing from the map. The capital-gains RATES and the charge schedule are no longer
# read here -- they belong to qbeast_exec's TaxEngine / ChargesCalculator, built from config.tax_config()
# and config.charges_config(). Importing them here again is how two copies of the law start to drift.
from qbeast_backtest.config import CASH_YIELD, SLIP_MAX

_CHARGES = None


def _charges():
    """The shared charges calculator, built once from config.charges_config()."""
    global _CHARGES
    if _CHARGES is None:
        from qbeast_backtest.config import charges_config
        from qbeast_exec import ChargesCalculator
        _CHARGES = ChargesCalculator(charges_config())
    return _CHARGES


_OHLCV_CACHE: dict[tuple, dict] = {}


def _ohlcv_frames(symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """OHLCV per symbol straight from the per-symbol CSVs in config.DATA_DIR.

    The dynamic model cannot be built from close+volume: Corwin-Schultz estimates the spread from the
    daily HIGH/LOW range, and ATR needs the true range. backtest.load_frames() drops both, so this reads
    the same validated per-symbol CSV the rest of the project trades on (data_io.load_ohlcv -> open,
    high, low, close, volume) rather than a second, looser parse of the same files.

    Cached per (data directory, symbol set): ~250-500 CSVs plus the rolling estimators is not free, and
    several scripts call liquidity_slippage() more than once per process."""
    from qbeast_backtest.config import DATA_DIR
    key = (str(DATA_DIR), symbols)
    hit = _OHLCV_CACHE.get(key)
    if hit is not None:
        return hit
    from qbeast_backtest import data_io
    out: dict[str, pd.DataFrame] = {}
    for s in symbols:
        try:
            f = data_io.load_ohlcv(s)
        except Exception:                                    # noqa: BLE001  (missing/invalid -> uncovered)
            continue
        if {"high", "low", "close"}.issubset(f.columns):
            out[s.upper()] = f
    _OHLCV_CACHE[key] = out
    return out


class SlippageRates(dict):
    """Per-symbol slippage rates that can ALSO answer the full per-(symbol, date, size) question.

    It subclasses dict because ~40 research callers treat liquidity_slippage()'s result as a flat
    {symbol: rate} map and read it with .get(). Those keep working unchanged: the dict values are the
    EXPECTED rate for that symbol (in dynamic mode, the median mu over the window at the strategy's own
    sleeve size), so a caller that cannot supply a date or an order size still gets a real number.

    Callers that DO know the fill -- the Ledger above all -- should call .rate(symbol, date, qty, price),
    which in dynamic mode evaluates the genuine size-aware model for THAT fill: Corwin-Schultz half
    spread + sqrt-impact against that day's ADV. A flat map cannot carry that, which is the whole reason
    this type exists rather than a plain dict.

    .rate() never returns zero silently. If the model has no data for a (symbol, date) it falls back to
    the per-symbol expectation, and symbols the model could not cover at all carry their liquidity-rank
    rate instead -- a missing HIGH/LOW must never read as a free trade."""

    def __init__(self, rates, model=None, mode: str = "rank", fallback: float | None = None,
                 covered: int = 0) -> None:
        super().__init__(rates)
        self.model = model
        self.mode = str(mode)
        self.covered = int(covered)
        self.fallback = float(SLIP_MAX if fallback is None else fallback)

    def rate(self, symbol: str, date=None, qty: float = 0.0, price: float = 0.0) -> float:
        """The slippage FRACTION for one prospective fill."""
        s = str(symbol).upper()
        if self.model is not None and date is not None and price and float(price) > 0:
            mu = float(self.model.terms(s, date, abs(float(qty)), float(price)).get("mu", 0.0))
            if np.isfinite(mu) and mu > 0:
                return mu
        return float(self.get(s, self.fallback))


def liquidity_slippage(closes: pd.DataFrame, volumes: pd.DataFrame) -> SlippageRates:
    """Slippage rates for this universe, from the SINGLE shared implementation in qbeast_exec.

    This was a local copy of the same liquidity ranking, which is exactly why a "slippage now lives in
    qbeast_exec" migration could look finished while ~40 research scripts still called the duplicate.
    It now delegates to qbeast_exec.SlippageModel under config.slippage_config(); "rank" mode is
    identical to the old local formula (most liquid by median traded value -> SLIP_MIN, least ->
    SLIP_MAX, linear in rank), so the published numbers are unchanged.

    DYNAMIC MODE. The returned object is still a {symbol: rate} mapping, but it also carries the live
    size-aware model (see SlippageRates.rate). The per-symbol values are the median mu across the window
    priced at one equal-weight sleeve, INITIAL_CAPITAL / TOP_N -- i.e. what this strategy's own order
    size actually costs, not a rate read off a single snapshot date. Because the dynamic model is built
    so that E[eps] = mu exactly, charging mu is the expectation of its two-sided draw: the mean cost is
    right, only the per-fill dispersion is averaged away.

    Symbols whose CSV has no usable HIGH/LOW (or too little history to warm the 21-day estimators) fall
    back to their liquidity-rank rate. Coverage is asserted, not assumed: if the dynamic model cannot
    price most of the universe this raises, because a silently zero-slippage backtest is the failure
    this function exists to prevent."""
    from dataclasses import replace

    from qbeast_backtest.config import SLIPPAGE_MODE, slippage_config
    from qbeast_exec import SlippageModel

    mode = str(SLIPPAGE_MODE).lower()
    if mode not in ("rank", "fixed", "none", "dynamic"):
        raise ValueError(f"SLIPPAGE_MODE={SLIPPAGE_MODE!r} is not one of "
                         f"'dynamic' / 'rank' / 'fixed' / 'none'.")

    cfg = slippage_config()
    cols = list(closes.columns)
    last = closes.index[-1] if len(closes.index) else None

    # close+volume is all the RANK/FIXED/NONE models need, and building the light frames also gives the
    # liquidity ranking used as the dynamic fallback below.
    light = {s: pd.DataFrame({"close": closes[s], "volume": volumes[s]}) for s in cols}
    rank_model = SlippageModel(light, replace(cfg, mode="rank"))
    rank_rate = {str(s).upper(): float(rank_model.terms(str(s), last)["mu"]) for s in cols}

    if mode != "dynamic":
        model = rank_model if mode == "rank" else SlippageModel(light, cfg)
        return SlippageRates({str(s).upper(): float(model.terms(str(s), last)["mu"]) for s in cols},
                             mode=mode, covered=len(cols))

    from qbeast_backtest.config import INITIAL_CAPITAL, TOP_N

    frames = _ohlcv_frames(tuple(sorted(str(s).upper() for s in cols)))
    model = SlippageModel(frames, cfg)
    sleeve = float(INITIAL_CAPITAL) / max(int(TOP_N), 1)      # notional of one equal-weight position

    # mu moves slowly, so sample ~24 dates across the window instead of every bar. This value is only
    # for callers that cannot supply a date; the Ledger asks the model per fill.
    idx = closes.index
    picks = idx[np.linspace(0, len(idx) - 1, 24).astype(int)] if len(idx) > 24 else idx

    rates, covered = {}, 0
    for s in cols:
        sym = str(s).upper()
        px_series, mus = closes[s], []
        for d in picks:
            try:
                px = float(px_series.asof(d))
            except Exception:                                # noqa: BLE001
                continue
            if not (np.isfinite(px) and px > 0):
                continue
            mu = float(model.terms(sym, d, sleeve / px, px).get("mu", 0.0))
            if np.isfinite(mu) and mu > 0:
                mus.append(mu)
        if mus:
            rates[sym] = float(np.median(mus))
            covered += 1
        else:
            rates[sym] = rank_rate.get(sym, SLIP_MAX)        # never zero: a missing HL is not a free trade
    if cols and covered < 0.5 * len(cols):
        raise ValueError(
            f"SLIPPAGE_MODE='dynamic' could only price {covered}/{len(cols)} symbols from "
            f"{len(frames)} OHLCV file(s) -- the rest have no usable HIGH/LOW or too little history, "
            f"and would trade at their liquidity-rank rate instead. Refusing rather than reporting a "
            f"mostly-static cost as dynamic. Check config.DATA_DIR, or set SLIPPAGE_MODE='rank'.")
    return SlippageRates(rates, model=model, mode=mode, covered=covered)


class Ledger:
    def __init__(self, capital: float, tax: bool = True, bands=None, staircase=None) -> None:
        self.tax_on = tax                                # False -> gross (no capital-gains tax deducted)
        # TIER-1 price-band gate (qbeast_exec.bands.PriceBandModel or None). A stock LOCKED at its circuit
        # can only be traded on one side: upper -> buyers only (cannot BUY), lower -> sellers only (cannot
        # SELL). Measured impact on this data is ~nil (real locks are ~0.001% of symbol-days), so it is a
        # realism/live-trading guard, not a backtest correction.
        self.bands = bands
        # STAIRCASE GATE (qbeast_exec.StaircaseFlag). Refuses BUYs in a name climbing its upper
        # circuit in steps. Momentum ranks those names top, so without this the strategy buys
        # the ladder at its most extended -- into a lock that has no sellers.
        self.staircase = staircase
        self.blocked_orders: list[dict] = []
        self.cash = float(capital)
        self.pos: dict[str, int] = {}
        # CAPITAL-GAINS TAX is the shared qbeast_exec.TaxEngine (config.tax_config()), not a second
        # implementation of the same law living here. It owns the FIFO lot book, the per-FY realised
        # split by sale-date regime, and the assessment; this class owns cash, positions and orders.
        from qbeast_backtest.config import tax_config
        from qbeast_exec import TaxEngine
        self.tax = TaxEngine(tax_config())
        self.tax_paid = self.cost_paid = self.interest = 0.0
        self.nav_hist: dict[pd.Timestamp, float] = {}
        self.invested_hist: dict[pd.Timestamp, float] = {}
        self.trades: list[dict] = []
        self.tax_events: list[dict] = []                 # FY capital-gains assessments (date, amounts)

    # --- views onto the shared tax ledger (kept so existing readers need no change) ---
    @property
    def lots(self) -> dict:
        """sym -> [Lot(date, qty, price), ...] FIFO. Lot is qbeast_exec's dataclass: use l.date/l.qty/
        l.price (it was a bare [date, qty, px] list before the tax engine was shared)."""
        return self.tax.lots

    @property
    def realized(self) -> dict:
        """fy -> {short_old, short_new, long_old, long_new} (gains split by sale-date tax regime)."""
        return self.tax.realized

    @property
    def assessed(self) -> set:
        return set(self.tax.assessed)

    @property
    def lot_closes(self) -> list[dict]:
        """Per closed FIFO slice, in this project's legacy key names (buy_px/sell_px)."""
        return [dict(symbol=c.symbol, buy_date=c.buy_date, sell_date=c.sell_date, days_held=c.days_held,
                     qty=c.qty, buy_px=c.buy_price, sell_px=c.sell_price, gain=c.gain, term=c.term)
                for c in self.tax.closes]

    @staticmethod
    def _fy(date: pd.Timestamp) -> int:
        return date.year if date.month >= 4 else date.year - 1

    def _accrue_and_assess(self, date: pd.Timestamp) -> None:
        d_int = self.cash * (CASH_YIELD / 252.0)         # one trading day of interest on idle cash
        self.cash += d_int
        self.interest += d_int
        if not self.tax_on:                              # gross run: skip the capital-gains deduction
            return
        # The shared TaxEngine decides WHAT is due and WHEN (once per FY, on/after 31 Mar); this class
        # only moves the cash and records the event for the reports.
        before = set(self.tax.assessed)
        due = self.tax.assess(date)
        if not due and not (set(self.tax.assessed) - before):
            return
        for fy in sorted(set(self.tax.assessed) - before):
            a = self.tax.assessed[fy]
            self.tax_events.append(dict(
                date=date, fy=fy, realised_stcg=a["realised_stcg"], realised_ltcg=a["realised_ltcg"],
                ltcg_exemption=a["ltcg_exemption_used"], stcg_taxable=a["stcg_taxable"],
                ltcg_taxable=a["ltcg_taxable"], stcg_tax=a["stcg_tax"], ltcg_tax=a["ltcg_tax"],
                tax=a["tax"],
                stcg_gain=a["stcg_taxable"], ltcg_gain=a["ltcg_taxable"],   # back-compat keys (daily_log.py)
                realised_stcg_old=a["stcg_old"], realised_stcg_new=a["stcg_new"],
                realised_ltcg_old=a["ltcg_old"], realised_ltcg_new=a["ltcg_new"],
                exemption_limit=a["ltcg_exemption_limit"]))                 # date-split detail for the FY statement
        self.cash -= due
        self.tax_paid += due

    def mark(self, date: pd.Timestamp, close_row: pd.Series) -> float:
        self._accrue_and_assess(date)
        mtm = sum(q * close_row.get(s, np.nan) for s, q in self.pos.items()
                  if np.isfinite(close_row.get(s, np.nan)))
        nav = self.cash + mtm
        self.nav_hist[date] = nav
        self.invested_hist[date] = mtm
        return nav

    def _nav_at(self, price_row: pd.Series) -> float:
        mtm = sum(q * price_row.get(s, np.nan) for s, q in self.pos.items()
                  if np.isfinite(price_row.get(s, np.nan)))
        return self.cash + mtm

    def _sell_fifo(self, s: str, qty: int, px: float, date: pd.Timestamp) -> tuple[float, float]:
        """Realise `qty` shares FIFO through the shared TaxEngine; returns (short_gain, long_gain)."""
        g = self.tax.sell(s, date, qty, px)
        return g["short"], g["long"]

    def execute(self, date: pd.Timestamp, weights: dict[str, float], open_row: pd.Series,
                slip: "SlippageRates | dict[str, float]", size_row: pd.Series | None = None) -> None:
        """Trade to target WEIGHTS at this day's open (weights -> shares against live NAV).

        `size_row` is the price row used to turn WEIGHTS INTO SHARE COUNTS. It defaults to `open_row`,
        which is the historical behaviour and keeps every published number bit-identical -- but that
        prices the order at the open it is about to fill at, i.e. it sizes with a price the order could
        not have known when it was sent. Passing the PRIOR CLOSE instead is what a real market-on-open
        order can do, and is what the Nautilus path already does (it submits on the index bar, before
        the day's stock bars arrive, so the newest price it can see is yesterday's close). Fills still
        happen at `open_row` either way; only the share count changes."""
        ref = open_row if size_row is None else size_row
        navref = self._nav_at(ref)
        targets = {}
        for s, w in weights.items():
            px = ref.get(s, np.nan)
            if np.isfinite(px) and px > 0:
                targets[s] = int(w * navref / px)
        for s in list(self.pos):                         # flatten anything held but no longer targeted
            if self.pos[s] and s not in targets:
                targets[s] = 0
        # SELLS (delta<0) execute before BUYS (delta>0): sale proceeds fund the purchases, so cash never
        # dips negative intraday (no implicit same-day margin). Final cash & positions are order-independent,
        # so the headline NAV/returns are unchanged -- this only makes the intraday cash path realistic.
        deltas = {}
        for s, tgt in targets.items():
            px = open_row.get(s, np.nan)
            if np.isfinite(px) and px > 0 and (tgt - self.pos.get(s, 0)) != 0:
                deltas[s] = tgt - self.pos.get(s, 0)
        for s in sorted(deltas, key=deltas.get):         # ascending delta => sells first, then buys
            delta = deltas[s]
            side = "BUY" if delta > 0 else "SELL"
            if self.bands is not None:                   # circuit-locked? the order cannot be executed
                if not self.bands.check(s, date, side, abs(delta)):
                    self.blocked_orders.append(dict(date=date, symbol=s, side=side, qty=abs(delta),
                                                    reason="circuit"))
                    continue                             # position is simply carried to the next rebalance
            if self.staircase is not None:               # operator ladder? do not add to it
                if not self.staircase.check(s, date, side, abs(delta)):
                    self.blocked_orders.append(dict(date=date, symbol=s, side=side, qty=abs(delta),
                                                    reason="staircase"))
                    continue
            px = open_row.get(s, np.nan)
            notional = abs(delta) * px
            # Charges come from the shared qbeast_exec calculator (config.charges_config()), not from a
            # rate multiplied in here. In "blended" mode that is COST_PCT x notional, identical to the
            # old line; in "itemised" mode it is the real STT / exchange / SEBI / stamp / GST schedule.
            commission = _charges().total("BUY" if delta > 0 else "SELL", abs(delta), px)
            # ASK PER FILL. In dynamic mode the rate depends on the DATE (that day's spread
            # and ADV) and on THIS order's size; a flat per-symbol rate cannot express either.
            rate = (slip.rate(s, date, abs(delta), px) if hasattr(slip, "rate")
                    else slip.get(s, SLIP_MAX))
            slippage = notional * rate
            cost = commission + slippage
            self.cash -= cost
            self.cost_paid += cost
            gain_short = gain_long = 0.0
            if delta > 0:
                self.cash -= delta * px
                self.tax.buy(s, date, delta, px)
                self.pos[s] = self.pos.get(s, 0) + delta
            else:
                self.cash += (-delta) * px
                gain_short, gain_long = self._sell_fifo(s, -delta, px, date)
                self.pos[s] = self.pos.get(s, 0) + delta
                if self.pos[s] == 0:
                    self.pos.pop(s, None)
            self.trades.append(dict(date=date, symbol=s, qty=delta, px=px, cost=cost,
                                    commission=commission, slippage=slippage,
                                    realized_short=gain_short, realized_long=gain_long))
