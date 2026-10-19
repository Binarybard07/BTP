r"""
qbeast_exec.tax — Indian capital-gains tax engine (shared, strategy-agnostic).

Implements listed-equity capital gains under s.111A (short-term) and s.112A (long-term), with the
DATE-DEPENDENT rates introduced by the Finance Act 2024 (effective 23-Jul-2024). The rate applying to a
gain is fixed by its SALE (transfer) date, not the purchase date:

                      | held < 365d (STCG) | held >= 365d (LTCG)
    sale <  23-Jul-24 |        15%         | 10% above Rs 1,00,000
    sale >= 23-Jul-24 |        20%         | 12.5% above Rs 1,25,000

Design notes
------------
* FIFO lots. A single sale can span several lots and can be PART short-term and PART long-term.
* Four buckets per financial year: {short, long} x {old, new regime}. This is what makes FY2024-25 --
  the year straddling the change -- computable.
* Losses net against gains WITHIN a year before any tax. Optional CARRY-FORWARD across years
  (s.74: STCL sets off STCG and LTCG; LTCL sets off only LTCG; 8 assessment years).
* Optional cess and surcharge (both default 0.0, i.e. base statutory rates only).
* No I/O, no globals, no dependency on any strategy's config. Feed it fills from anywhere -- a Nautilus
  backtest, a broker statement, a CSV.

Usage
-----
    from qbeast_exec.tax import TaxEngine, TaxConfig

    tax = TaxEngine(TaxConfig())
    tax.buy("RELIANCE", "2023-04-10", 100, 2400.0)
    tax.sell("RELIANCE", "2024-09-01", 100, 2900.0)     # -> realised gain, bucketed
    due = tax.assess("2025-03-31")                      # FY2024-25 tax payable
    df  = tax.statement()                               # one row per financial year
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

__all__ = ["TaxConfig", "TaxEngine", "Lot", "LotClose"]


# --------------------------------------------------------------------------- config
@dataclass
class TaxConfig:
    """All tax parameters. Defaults = Indian listed equity, base statutory rates, no cess/surcharge."""

    regime_change: pd.Timestamp = pd.Timestamp("2024-07-23")
    stcg_rate_old: float = 0.15         # sale before regime_change
    stcg_rate_new: float = 0.20         # sale on/after regime_change
    ltcg_rate_old: float = 0.10
    ltcg_rate_new: float = 0.125
    ltcg_exemption_old: float = 100_000.0   # annual, FY < exemption_change_fy
    ltcg_exemption_new: float = 125_000.0   # annual, FY >= exemption_change_fy
    exemption_change_fy: int = 2024         # FY2024-25 onwards
    # HOLDING PERIOD — s.2(42A) defines a listed equity share as SHORT-term when held for "not more than
    # 12 MONTHS", i.e. long-term needs MORE than 12 calendar months. That is NOT the same as 365 days:
    #   * a 12-month span containing 29-Feb is 366 days, so a 365-day rule turns it long-term a day EARLY;
    #   * months differ in length, so the day-count boundary drifts with the purchase date.
    # Calendar months are therefore the default. Set `ltcg_hold_days` to force a raw day-count instead.
    ltcg_hold_months: int = 12              # the statutory definition
    ltcg_hold_days: int | None = None       # override: raw day count (e.g. 365) — legacy behaviour
    ltcg_boundary_inclusive: bool = False   # False => strictly MORE than 12 months (the law).
                                            # True  => exactly 12 months already counts as long-term.
    fy_start_month: int = 4                 # Indian FY: 1 Apr -> 31 Mar

    # Optional refinements (all OFF by default so results match the base statutory computation)
    carry_forward_losses: bool = False   # s.74: STCL -> STCG+LTCG, LTCL -> LTCG only, 8 years
    carry_forward_years: int = 8
    cess: float = 0.0                    # e.g. 0.04 for 4% health & education cess ON the tax
    surcharge: float = 0.0               # e.g. 0.10; applied before cess

    def rates(self, sale_date) -> tuple[float, float]:
        """(stcg_rate, ltcg_rate) for a gain realised on `sale_date`."""
        new = pd.Timestamp(sale_date) >= self.regime_change
        return ((self.stcg_rate_new, self.ltcg_rate_new) if new
                else (self.stcg_rate_old, self.ltcg_rate_old))

    def exemption(self, fy: int) -> float:
        """Annual LTCG exemption for financial year `fy` (fy = starting calendar year)."""
        return self.ltcg_exemption_new if fy >= self.exemption_change_fy else self.ltcg_exemption_old

    def long_term_on_or_after(self, buy_date) -> pd.Timestamp:
        """The FIRST sale date on which a lot bought on `buy_date` qualifies as LONG-term.

        Calendar-month arithmetic (pandas `DateOffset`) handles month lengths and leap days correctly:
        31-Jan + 12m -> 31-Jan; 29-Feb-2024 + 12m -> 28-Feb-2025 (clamped)."""
        b = pd.Timestamp(buy_date).normalize()
        if self.ltcg_hold_days is not None:                        # raw day-count override
            return b + pd.Timedelta(days=int(self.ltcg_hold_days))
        anniversary = b + pd.DateOffset(months=int(self.ltcg_hold_months))
        # "not more than 12 months" is SHORT-term, so long-term starts the day AFTER the anniversary
        return anniversary if self.ltcg_boundary_inclusive else anniversary + pd.Timedelta(days=1)

    def is_long_term(self, buy_date, sell_date) -> bool:
        """True if a lot bought on `buy_date` and sold on `sell_date` is a LONG-term capital asset."""
        return pd.Timestamp(sell_date).normalize() >= self.long_term_on_or_after(buy_date)

    def gross_up(self, tax: float) -> float:
        """Apply surcharge then cess to a base tax amount."""
        return tax * (1.0 + self.surcharge) * (1.0 + self.cess)


@dataclass
class Lot:
    """An open purchase lot (FIFO queue element)."""
    date: pd.Timestamp
    qty: float
    price: float


@dataclass
class LotClose:
    """A realised slice: part of a sale matched against part of a lot."""
    symbol: str
    buy_date: pd.Timestamp
    sell_date: pd.Timestamp
    days_held: int
    qty: float
    buy_price: float
    sell_price: float
    gain: float
    term: str            # "STCG" | "LTCG"
    regime: str          # "old" | "new"  (by SALE date)


# --------------------------------------------------------------------------- engine
class TaxEngine:
    """FIFO capital-gains ledger. Stateless w.r.t. cash: it reports what tax is DUE; the caller decides
    when to deduct it (see `assess`)."""

    def __init__(self, config: TaxConfig | None = None) -> None:
        self.cfg = config or TaxConfig()
        self.lots: dict[str, list[Lot]] = {}
        self.closes: list[LotClose] = []
        # fy -> {"short_old","short_new","long_old","long_new"}
        self.realized: dict[int, dict[str, float]] = {}
        self.assessed: dict[int, dict] = {}          # fy -> assessment detail
        self.carried: dict[int, dict[str, float]] = {}   # fy -> unabsorbed {"stcl","ltcl"} to carry forward

    # ---- calendar
    def fy(self, date) -> int:
        """Financial year (starting calendar year). 15-Feb-2025 -> FY2024 (i.e. FY2024-25)."""
        d = pd.Timestamp(date)
        return d.year if d.month >= self.cfg.fy_start_month else d.year - 1

    @staticmethod
    def fy_label(fy: int) -> str:
        return f"FY {fy}-{str(fy + 1)[-2:]}"

    def fy_end(self, fy: int) -> pd.Timestamp:
        return pd.Timestamp(fy + 1, self.cfg.fy_start_month, 1) - pd.Timedelta(days=1)

    # ---- trades
    def buy(self, symbol: str, date, qty: float, price: float) -> None:
        """Record a purchase. Creates a FIFO lot. `qty` must be positive."""
        if qty <= 0:
            raise ValueError(f"buy qty must be > 0, got {qty}")
        self.lots.setdefault(symbol.upper(), []).append(Lot(pd.Timestamp(date).normalize(), float(qty), float(price)))

    def sell(self, symbol: str, date, qty: float, price: float) -> dict[str, float]:
        """Record a sale, matching FIFO against open lots. Returns the gains realised on THIS sale:
        {"short": x, "long": y}. Selling more than is held raises ValueError."""
        if qty <= 0:
            raise ValueError(f"sell qty must be > 0, got {qty}")
        sym = symbol.upper()
        d = pd.Timestamp(date).normalize()
        book = self.lots.get(sym, [])
        held = sum(l.qty for l in book)
        if qty > held + 1e-9:
            raise ValueError(f"{sym}: selling {qty} but only {held} held on {d.date()} (no shorting)")
        regime = "new" if d >= self.cfg.regime_change else "old"
        fy = self.fy(d)
        r = self.realized.setdefault(fy, {"short_old": 0.0, "short_new": 0.0,
                                          "long_old": 0.0, "long_new": 0.0})
        remaining, out = float(qty), {"short": 0.0, "long": 0.0}
        while remaining > 1e-9 and book:
            lot = book[0]
            take = min(remaining, lot.qty)
            gain = take * (float(price) - lot.price)
            days = (d - lot.date).days
            is_long = self.cfg.is_long_term(lot.date, d)           # calendar months, not a day count
            self.closes.append(LotClose(sym, lot.date, d, days, take, lot.price, float(price),
                                        gain, "LTCG" if is_long else "STCG", regime))
            r[("long_" if is_long else "short_") + regime] += gain
            out["long" if is_long else "short"] += gain
            lot.qty -= take
            remaining -= take
            if lot.qty <= 1e-9:
                book.pop(0)
        return out

    # ---- assessment
    def _compute(self, fy: int) -> dict:
        """Tax for one financial year. Pure: does not mutate carry-forward state."""
        r = self.realized.get(fy, {"short_old": 0.0, "short_new": 0.0, "long_old": 0.0, "long_new": 0.0})
        so, sn, lo, ln = r["short_old"], r["short_new"], r["long_old"], r["long_new"]
        stcg_net, ltcg_net = so + sn, lo + ln

        # brought-forward losses (positive numbers = losses available to absorb gains)
        bf = {"stcl": 0.0, "ltcl": 0.0}
        if self.cfg.carry_forward_losses:
            for y in range(fy - self.cfg.carry_forward_years, fy):
                c = self.carried.get(y)
                if c:
                    bf["stcl"] += c.get("stcl", 0.0)
                    bf["ltcl"] += c.get("ltcl", 0.0)

        # s.74 set-off order: LTCL absorbs LTCG only; STCL absorbs STCG first, then LTCG.
        stcg_after, ltcg_after = stcg_net, ltcg_net
        used_ltcl = min(max(bf["ltcl"], 0.0), max(ltcg_after, 0.0))
        ltcg_after -= used_ltcl
        used_stcl_on_stcg = min(max(bf["stcl"], 0.0), max(stcg_after, 0.0))
        stcg_after -= used_stcl_on_stcg
        used_stcl_on_ltcg = min(max(bf["stcl"] - used_stcl_on_stcg, 0.0), max(ltcg_after, 0.0))
        ltcg_after -= used_stcl_on_ltcg

        # STCG: no exemption. Split the positive net across old/new rate portions (old consumed first).
        stcg_taxable = max(stcg_after, 0.0)
        stcg_old_t = min(max(so, 0.0), stcg_taxable)
        stcg_new_t = stcg_taxable - stcg_old_t
        stcg_tax = stcg_old_t * self.cfg.stcg_rate_old + stcg_new_t * self.cfg.stcg_rate_new

        # LTCG: annual exemption on the net, consumed chronologically (old-regime gains first).
        exemption = self.cfg.exemption(fy)
        ltcg_positive = max(ltcg_after, 0.0)
        exemption_used = min(ltcg_positive, exemption)
        ltcg_taxable = max(ltcg_positive - exemption, 0.0)
        ltcg_new_t = min(ltcg_taxable, max(ln, 0.0))
        ltcg_old_t = ltcg_taxable - ltcg_new_t
        ltcg_tax = ltcg_old_t * self.cfg.ltcg_rate_old + ltcg_new_t * self.cfg.ltcg_rate_new

        base = stcg_tax + ltcg_tax
        total = self.cfg.gross_up(base)

        # losses left unabsorbed this year -> carried forward
        carry = {"stcl": max(-stcg_after, 0.0), "ltcl": max(-ltcg_after, 0.0)} \
            if self.cfg.carry_forward_losses else {"stcl": 0.0, "ltcl": 0.0}

        return dict(
            fy=fy, fy_label=self.fy_label(fy), fy_end=self.fy_end(fy),
            realised_stcg=stcg_net, realised_ltcg=ltcg_net,
            stcg_old=so, stcg_new=sn, ltcg_old=lo, ltcg_new=ln,
            bf_stcl=bf["stcl"], bf_ltcl=bf["ltcl"],
            loss_setoff=used_ltcl + used_stcl_on_stcg + used_stcl_on_ltcg,
            ltcg_exemption_limit=exemption, ltcg_exemption_used=exemption_used,
            stcg_taxable=stcg_taxable, ltcg_taxable=ltcg_taxable,
            stcg_tax=stcg_tax, ltcg_tax=ltcg_tax, base_tax=base,
            surcharge_and_cess=total - base, tax=total,
            carry_forward_stcl=carry["stcl"], carry_forward_ltcl=carry["ltcl"])

    def assess(self, date) -> float:
        """Assess every financial year that has ENDED on or before `date` and is not yet assessed.
        Returns the total tax becoming payable at this call (0.0 if nothing is due).

        Call this on each simulated day; it fires at most once per financial year."""
        d = pd.Timestamp(date)
        due = 0.0
        for fy in sorted(self.realized):
            if fy in self.assessed or d < self.fy_end(fy):
                continue
            a = self._compute(fy)
            a["date_assessed"] = d
            self.assessed[fy] = a
            self.carried[fy] = {"stcl": a["carry_forward_stcl"], "ltcl": a["carry_forward_ltcl"]}
            due += a["tax"]
        return due

    def tax_for(self, fy: int) -> float:
        """Tax for one financial year without recording an assessment (a what-if)."""
        return self._compute(fy)["tax"]

    # ---- reporting
    def statement(self) -> pd.DataFrame:
        """One row per assessed financial year. Assess first (or call `assess_all`)."""
        if not self.assessed:
            return pd.DataFrame()
        return pd.DataFrame([self.assessed[fy] for fy in sorted(self.assessed)])

    def assess_all(self) -> pd.DataFrame:
        """Assess every financial year with realised gains, regardless of date. Returns the statement."""
        for fy in sorted(self.realized):
            if fy not in self.assessed:
                a = self._compute(fy)
                a["date_assessed"] = self.fy_end(fy)
                self.assessed[fy] = a
                self.carried[fy] = {"stcl": a["carry_forward_stcl"], "ltcl": a["carry_forward_ltcl"]}
        return self.statement()

    def closed_lots(self) -> pd.DataFrame:
        """Every realised slice: symbol, buy/sell dates, days held, qty, prices, gain, term, regime."""
        return pd.DataFrame([c.__dict__ for c in self.closes])

    def open_lots(self) -> pd.DataFrame:
        """Currently held lots (unrealised)."""
        rows = [dict(symbol=s, date=l.date, qty=l.qty, price=l.price)
                for s, book in self.lots.items() for l in book if l.qty > 1e-9]
        return pd.DataFrame(rows)

    def position(self, symbol: str) -> float:
        return sum(l.qty for l in self.lots.get(symbol.upper(), []))

    def holding_period_summary(self) -> pd.DataFrame:
        """Per-symbol median/mean holding period and the STCG vs LTCG split of realised gains."""
        df = self.closed_lots()
        if df.empty:
            return pd.DataFrame()
        g = df.groupby("symbol")
        out = pd.DataFrame({
            "trades": g.size(),
            "median_days": g["days_held"].median(),
            "mean_days": g["days_held"].mean(),
            "gain": g["gain"].sum(),
            "stcg_gain": df[df.term == "STCG"].groupby("symbol")["gain"].sum(),
            "ltcg_gain": df[df.term == "LTCG"].groupby("symbol")["gain"].sum(),
        }).fillna(0.0)
        return out.sort_values("gain", ascending=False)

    # ---- adapters
    def apply_fills(self, fills, symbol="symbol", date="date", qty="qty", price="price") -> None:
        """Bulk-load fills from a DataFrame or list of dicts, in chronological order.

        `qty` is SIGNED: positive = buy, negative = sell. Column names are overridable, so this accepts
        a Nautilus fill export, a broker contract-note dump, or any tabular source."""
        df = pd.DataFrame(fills) if not isinstance(fills, pd.DataFrame) else fills.copy()
        if df.empty:
            return
        df[date] = pd.to_datetime(df[date])
        for _, r in df.sort_values(date).iterrows():
            q = float(r[qty])
            if q > 0:
                self.buy(str(r[symbol]), r[date], q, float(r[price]))
            elif q < 0:
                self.sell(str(r[symbol]), r[date], -q, float(r[price]))
