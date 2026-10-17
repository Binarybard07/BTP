import datetime
from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional, Set

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
    Manages DP charges with daily per-security/ISIN debit frequency tracking.

    Rules:
    - Triggers strictly on delivery sells (never on buys).
    - Charged at most once per security/ISIN per trading day, regardless of multiple orders or fills.
    - Zero charge for buys or subsequent sells of the same security on the same day.
    """

    def __init__(self, config: Optional[DPChargesConfig] = None):
        self.config = config or DPChargesConfig()
        # Mapping: date -> set of debited security identifiers (ISIN or symbol) for that date
        self._debited_by_date: Dict[datetime.date, Set[str]] = {}

    def reset(self) -> None:
        """Clears all tracking state."""
        self._debited_by_date.clear()

    def evaluate_dp_charge(
        self,
        security_id: str,
        trading_date: datetime.date,
        is_sell: bool,
        fill_quantity: float,
    ) -> float:
        """
        Determines DP charge for a given fill.

        Parameters:
            security_id: ISIN or symbol identifier for the equity instrument.
            trading_date: Trading session date (calendar date in exchange timezone).
            is_sell: True if fill is for a SELL order, False for BUY.
            fill_quantity: Executed fill quantity.

        Returns:
            float: ₹15.34 if this is the first qualifying delivery sell of the security on trading_date,
                   0.0 otherwise.
        """
        if not is_sell or fill_quantity <= 0:
            return 0.0

        if trading_date not in self._debited_by_date:
            self._debited_by_date[trading_date] = set()

        clean_id = str(security_id).strip().upper()
        if clean_id in self._debited_by_date[trading_date]:
            # Already debited today for this security -> at most once per trading day
            logger.debug(
                "DP charge skipped for %s on %s: already charged today",
                clean_id,
                trading_date,
            )
            return 0.0

        # First qualifying delivery sell of this security on this trading day
        self._debited_by_date[trading_date].add(clean_id)
        charge = self.config.total_dp_charge

        logger.info(
            "DP CHARGE APPLIED: INR %.2f on %s for %s (First delivery sell of day, qty=%.2f)",
            charge,
            trading_date,
            clean_id,
            fill_quantity,
        )
        return charge
