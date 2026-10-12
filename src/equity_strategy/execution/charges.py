import datetime
from dataclasses import dataclass, field
import logging
import math
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrdinaryChargesConfig:
    """
    Configuration parameters for Indian NSE equity delivery transaction charges.

    Rates are based on standard NSE cash-delivery transaction rules:
    - Brokerage: 0% by default (configurable)
    - STT (Securities Transaction Tax): 0.10% on buy turnover and 0.10% on sell turnover
    - NSE transaction charge: 0.00307% on eligible turnover
    - SEBI turnover fee: ₹10 per crore of turnover (0.0001% = ₹10 / 10,000,000)
    - Stamp duty: 0.015% on buy turnover; 0% on sell turnover
    - GST: 18% on (brokerage + exchange transaction charge + SEBI fee)
    - Currency: 'INR'
    """

    brokerage_pct: float = 0.0
    stt_buy_pct: float = 0.0010
    stt_sell_pct: float = 0.0010
    nse_txn_pct: float = 0.0000307
    sebi_fee_per_crore: float = 10.0
    stamp_duty_buy_pct: float = 0.00015
    stamp_duty_sell_pct: float = 0.0
    gst_pct: float = 0.18
    currency: str = "INR"

    @property
    def sebi_fee_pct(self) -> float:
        """Computes SEBI turnover fee as a fraction of turnover (₹10 / 10,000,000 = 0.000001)."""
        return self.sebi_fee_per_crore / 10_000_000.0


@dataclass(frozen=True)
class OrdinaryChargesBreakdown:
    """
    Itemised breakdown of ordinary transaction charges for an individual fill.
    All itemised components use a documented 2-decimal rounding policy and reconcile
    strictly with the total ordinary charges.
    """

    turnover: float
    is_buy: bool
    brokerage: float
    stt: float
    nse_txn: float
    sebi_fee: float
    stamp_duty: float
    taxable_base: float
    gst: float
    total_ordinary: float


class OrdinaryChargesCalculator:
    """
    Pure calculation engine for Indian equity delivery ordinary transaction charges.
    Independent of NautilusTrader execution structures.

    Rounding Policy:
    Each component is rounded to 2 decimal places (paise) using standard round-half-to-even.
    GST is calculated on the rounded taxable base (brokerage + nse_txn + sebi_fee).
    The total ordinary charges strictly equal the sum of the itemised rounded components:
        total_ordinary = brokerage + stt + nse_txn + sebi_fee + stamp_duty + gst
    """

    def __init__(self, config: Optional[OrdinaryChargesConfig] = None):
        self.config = config or OrdinaryChargesConfig()

    def calculate(
        self,
        quantity: float,
        price: float,
        is_buy: bool,
    ) -> OrdinaryChargesBreakdown:
        """
        Calculates ordinary transaction charges for a single fill.

        Parameters:
            quantity: Fill quantity (positive float).
            price: Fill execution price.
            is_buy: True for BUY order, False for SELL order.

        Returns:
            OrdinaryChargesBreakdown with all itemised charges and reconciled total.
        """
        turnover = quantity * price

        brokerage = round(turnover * self.config.brokerage_pct, 2)
        stt_rate = self.config.stt_buy_pct if is_buy else self.config.stt_sell_pct
        stt = round(turnover * stt_rate, 2)

        nse_txn = round(turnover * self.config.nse_txn_pct, 2)
        sebi_fee = round(turnover * self.config.sebi_fee_pct, 2)

        stamp_rate = self.config.stamp_duty_buy_pct if is_buy else self.config.stamp_duty_sell_pct
        stamp_duty = round(turnover * stamp_rate, 2)

        taxable_base = round(brokerage + nse_txn + sebi_fee, 2)
        gst = round(taxable_base * self.config.gst_pct, 2)

        total_ordinary = round(
            brokerage + stt + nse_txn + sebi_fee + stamp_duty + gst,
            2,
        )

        return OrdinaryChargesBreakdown(
            turnover=round(turnover, 2),
            is_buy=is_buy,
            brokerage=brokerage,
            stt=stt,
            nse_txn=nse_txn,
            sebi_fee=sebi_fee,
            stamp_duty=stamp_duty,
            taxable_base=taxable_base,
            gst=gst,
            total_ordinary=total_ordinary,
        )


@dataclass(frozen=True)
class DPChargesConfig:
    """
    Configuration parameters for Depository Participant (DP) charges.

    Default schedule:
    - Broker component: ₹9.50
    - Depository component (CDSL/NSDL): ₹3.50
    - GST rate: 18% on (broker component + depository component)
    - Total DP charge: ₹9.50 + ₹3.50 + ₹2.34 (18% of 13.00) = ₹15.34 per qualifying ISIN per trading day.
    """

    broker_component: float = 9.50
    depository_component: float = 3.50
    gst_pct: float = 0.18
    currency: str = "INR"

    @property
    def total_dp_charge(self) -> float:
        """Computes total DP charge including GST."""
        taxable = round(self.broker_component + self.depository_component, 2)
        gst = round(taxable * self.gst_pct, 2)
        return round(taxable + gst, 2)


class DPChargesTracker:
    """
    Manages DP charges with daily per-ISIN debit frequency tracking.

    Rules:
    - Triggers only when a delivery sell causes shares of an ISIN to be debited.
    - Charged at most once per ISIN per trading day, regardless of multiple orders or fills.
    - Zero charge for buys.
    - Operates under the strategy's cash-delivery assumption.
    """

    def __init__(self, config: Optional[DPChargesConfig] = None):
        self.config = config or DPChargesConfig()
        # Mapping: date -> set of debited ISIN/symbols for that date
        self._debited_isins_by_date: Dict[datetime.date, Set[str]] = {}
        self._dp_charge_records: List[Dict[str, Any]] = []

    def reset(self) -> None:
        """Clears all tracking state."""
        self._debited_isins_by_date.clear()
        self._dp_charge_records.clear()

    def evaluate_dp_charge(
        self,
        isin_symbol: str,
        trading_date: datetime.date,
        is_sell: bool,
        fill_quantity: float,
    ) -> float:
        """
        Determines DP charge for a given fill.

        Parameters:
            isin_symbol: Symbol or ISIN identifier for the equity instrument.
            trading_date: Trading session date (calendar date in IST).
            is_sell: True if fill is for a SELL order, False for BUY.
            fill_quantity: Executed fill quantity.

        Returns:
            float: ₹15.34 if this is the first qualifying delivery sell of the ISIN on trading_date,
                   0.0 otherwise (buys, or subsequent sells of the same ISIN on the same day).
        """
        if not is_sell or fill_quantity <= 0:
            return 0.0

        if trading_date not in self._debited_isins_by_date:
            self._debited_isins_by_date[trading_date] = set()

        clean_symbol = isin_symbol.strip().upper()
        if clean_symbol in self._debited_isins_by_date[trading_date]:
            # Already charged today for this ISIN -> charge at most once per trading day
            logger.debug(
                "DP charge skipped for %s on %s: already charged for this ISIN today",
                clean_symbol,
                trading_date,
            )
            return 0.0

        # First qualifying sell of this ISIN on this trading day
        self._debited_isins_by_date[trading_date].add(clean_symbol)
        charge = self.config.total_dp_charge

        taxable = round(self.config.broker_component + self.config.depository_component, 2)
        gst = round(taxable * self.config.gst_pct, 2)

        self._dp_charge_records.append({
            "date": trading_date,
            "symbol": clean_symbol,
            "fill_quantity": fill_quantity,
            "broker_component": self.config.broker_component,
            "depository_component": self.config.depository_component,
            "gst": gst,
            "total_dp_charge": charge,
        })

        logger.info(
            "DP CHARGE APPLIED: ₹%.2f on %s for ISIN %s (First delivery sell of day, qty=%.2f)",
            charge,
            trading_date,
            clean_symbol,
            fill_quantity,
        )
        return charge

    @property
    def total_dp_charges_incurred(self) -> float:
        """Total DP charges incurred across all tracked sessions."""
        return sum(rec["total_dp_charge"] for rec in self._dp_charge_records)

    @property
    def records(self) -> List[Dict[str, Any]]:
        """List of all DP charge event records."""
        return list(self._dp_charge_records)
