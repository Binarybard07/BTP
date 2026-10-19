"""
Nautilus SimulationModule for Indian Equity Tax debit accounting.
"""
import logging
from typing import Any, Dict, List, Optional, Set
import pandas as pd

from nautilus_trader.backtest import SimulationModule, SimulationModuleContext
from nautilus_trader.model import Currency, Money, OrderFilled, OrderSide

from equity_strategy.tax.accounting import AnnualTaxRecord
from equity_strategy.tax.lot import FIFOMatcher

logger = logging.getLogger(__name__)


class IndianEquityTaxModule(SimulationModule):
    """
    SimulationModule for Indian Equity Tax accounting on NautilusTrader.

    Records fills, matches lots FIFO, nets annual capital gains/losses,
    and applies a post-cutoff account debit on the simulated account
    at the Indian financial year boundary (31 March cutoff).
    """

    def __init__(self, currency: str = "INR") -> None:
        super().__init__()
        self.currency: str = currency
        self.matcher: FIFOMatcher = FIFOMatcher()
        self.fy_records: Dict[int, AnnualTaxRecord] = {}
        self.settled_fys: Set[int] = set()

    @staticmethod
    def get_fy_for_ts(ts_ns: int) -> int:
        """
        Returns the financial year ending year in Indian Standard Time.
        E.g., 2014-04-01 to 2015-03-31 belongs to FY 2014-15 ending in 2015.
        """
        dt = pd.Timestamp(ts_ns, unit="ns", tz="UTC").tz_convert("Asia/Kolkata")
        return dt.year if dt.month <= 3 else dt.year + 1

    @staticmethod
    def get_fy_cutoff_ns(fy_year: int) -> int:
        """
        Returns the 31 March 23:59:59.999999999 IST cutoff in UTC nanoseconds.
        """
        ts = pd.Timestamp(f"{fy_year}-03-31 23:59:59.999999999", tz="Asia/Kolkata").tz_convert("UTC")
        return int(ts.value)

    def record_fill(self, event: OrderFilled) -> None:
        """
        Records an executed OrderFilled event from strategy execution.
        """
        inst_id = event.instrument_id
        qty = float(event.last_qty.as_double())
        px = float(event.last_px.as_double())
        ts = event.ts_event

        if event.order_side == OrderSide.BUY:
            self.matcher.add_buy(
                instrument_id=inst_id,
                quantity=qty,
                price=px,
                ts_event=ts,
            )
        elif event.order_side == OrderSide.SELL:
            matched_trades = self.matcher.match_sell(
                instrument_id=inst_id,
                quantity=qty,
                price=px,
                ts_event=ts,
            )
            for trade in matched_trades:
                fy = self.get_fy_for_ts(trade.sell_ts)
                if fy not in self.fy_records:
                    self.fy_records[fy] = AnnualTaxRecord(fy_year=fy)
                self.fy_records[fy].add_realised_trade(
                    is_long_term=trade.is_long_term,
                    pnl=trade.pnl,
                )

    def process(
        self,
        ts_now: int,
        context: SimulationModuleContext,
    ) -> Optional[List[Money]]:
        """
        Nautilus SimulationModule lifecycle hook called on each simulation step.
        Debits annual tax for any completed financial year whose 31 March cutoff has passed.
        If the backtest ends on 31 March or before the next event after cutoff, no settlement occurs.
        """
        current_fy = self.get_fy_for_ts(ts_now)

        candidate_fys = set(self.fy_records.keys())
        if candidate_fys:
            min_fy = min(candidate_fys)
            for y in range(min_fy, current_fy):
                candidate_fys.add(y)

        debits: List[Money] = []

        for fy in sorted(candidate_fys):
            if fy in self.settled_fys:
                continue

            cutoff = self.get_fy_cutoff_ns(fy)
            if ts_now > cutoff:
                self.settled_fys.add(fy)
                if fy in self.fy_records:
                    tax_info = self.fy_records[fy].calculate_tax()
                    tax_amount = tax_info["total_tax"]
                    if tax_amount > 0.0:
                        logger.info(
                            "Debiting annual Indian Equity Tax for FY %d-%d (ending %d-03-31): %.2f %s "
                            "(STCG Tax: %.2f, LTCG Tax: %.2f)",
                            fy - 1,
                            fy,
                            fy,
                            tax_amount,
                            self.currency,
                            tax_info["stcg_tax"],
                            tax_info["ltcg_tax"],
                        )
                        debits.append(Money(-tax_amount, Currency.from_str(self.currency)))

        return debits if debits else None

    def acknowledge(self, outcomes: Any) -> None:
        """
        Nautilus SimulationModule lifecycle hook receiving account adjustment status.
        """
        if outcomes:
            for outcome in outcomes:
                if getattr(outcome, "applied", False):
                    logger.info("Tax account adjustment applied successfully.")
                elif getattr(outcome, "error", None):
                    logger.error("Tax account adjustment failed: %s", outcome.error)

    def reset(self) -> None:
        """Resets tax tracking and settlement state."""
        self.matcher.clear()
        self.fy_records.clear()
        self.settled_fys.clear()


__all__ = [
    "IndianEquityTaxModule",
]
