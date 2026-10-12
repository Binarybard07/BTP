from dataclasses import dataclass
from typing import Any, Dict, Optional
import pandas as pd

from equity_strategy.execution.fee_model import IndianEquityDeliveryFeeModel
from equity_strategy.execution.slippage import AdverseSlippageFillModel


class ExecutionCostReport:
    """
    Consolidated reporting engine for Indian equity transaction charges, DP charges, and slippage.

    Produces structured summaries, itemised breakdowns, and human-readable audit reports
    reconciled with NautilusTrader's native cash and equity accounting.
    """

    def __init__(
        self,
        fee_model: Optional[IndianEquityDeliveryFeeModel] = None,
        fill_model: Optional[AdverseSlippageFillModel] = None,
    ) -> None:
        self.fee_model = fee_model
        self.fill_model = fill_model

    def to_dict(self) -> Dict[str, Any]:
        """Returns complete execution cost metrics as a nested dictionary."""
        # Turnover & Volumes
        total_turnover = self.fee_model.total_turnover if self.fee_model else 0.0
        buy_turnover = self.fee_model.buy_turnover if self.fee_model else 0.0
        sell_turnover = self.fee_model.sell_turnover if self.fee_model else 0.0
        fill_count = self.fee_model.fill_count if self.fee_model else 0

        # Itemised Ordinary Statutory Charges
        brokerage = self.fee_model.total_brokerage if self.fee_model else 0.0
        stt = self.fee_model.total_stt if self.fee_model else 0.0
        nse_txn = self.fee_model.total_nse_txn if self.fee_model else 0.0
        sebi_fee = self.fee_model.total_sebi_fee if self.fee_model else 0.0
        stamp_duty = self.fee_model.total_stamp_duty if self.fee_model else 0.0
        gst = self.fee_model.total_gst if self.fee_model else 0.0
        total_ordinary = self.fee_model.total_ordinary_charges if self.fee_model else 0.0

        # DP Charges
        dp_charges = self.fee_model.total_dp_charges if self.fee_model else 0.0
        dp_events_count = (
            len(self.fee_model.dp_tracker.records)
            if self.fee_model and hasattr(self.fee_model, "dp_tracker")
            else 0
        )

        # Total Commission (Statutory Ordinary + DP)
        total_commission = self.fee_model.total_commission if self.fee_model else 0.0

        # Slippage Cost
        slippage_cost = self.fill_model.total_slippage_cost if self.fill_model else 0.0
        slippage_pct = self.fill_model.slippage_pct if self.fill_model else 0.0

        # Grand Total Execution Drag
        total_execution_drag = round(total_commission + slippage_cost, 2)
        drag_bps = (
            round((total_execution_drag / total_turnover) * 10_000, 2)
            if total_turnover > 0
            else 0.0
        )

        return {
            "turnover": {
                "total_turnover": total_turnover,
                "buy_turnover": buy_turnover,
                "sell_turnover": sell_turnover,
                "fill_count": fill_count,
            },
            "ordinary_charges": {
                "brokerage": brokerage,
                "stt": stt,
                "nse_txn": nse_txn,
                "sebi_fee": sebi_fee,
                "stamp_duty": stamp_duty,
                "gst": gst,
                "total_ordinary": total_ordinary,
            },
            "dp_charges": {
                "total_dp_charges": dp_charges,
                "qualifying_isin_days_count": dp_events_count,
                "rate_per_isin_day": (
                    self.fee_model.dp_config.total_dp_charge if self.fee_model else 15.34
                ),
            },
            "slippage": {
                "slippage_rate_pct": slippage_pct,
                "total_slippage_cost": slippage_cost,
                "slipped_fill_count": self.fill_model.fill_count if self.fill_model else 0,
            },
            "summary": {
                "total_commission_deducted": total_commission,
                "total_slippage_drag": slippage_cost,
                "grand_total_execution_drag": total_execution_drag,
                "drag_basis_points": drag_bps,
            },
        }

    def to_text(self) -> str:
        """Formats a human-readable execution cost report."""
        d = self.to_dict()
        t = d["turnover"]
        o = d["ordinary_charges"]
        dp = d["dp_charges"]
        s = d["slippage"]
        sm = d["summary"]

        lines = [
            "================================================================================",
            "                   EXECUTION TRANSACTION COSTS & FRICTION REPORT                ",
            "================================================================================",
            f"Fills Tracked:               {t['fill_count']}",
            f"Total Turnover:              ₹{t['total_turnover']:>15,.2f}",
            f"  - Buy Turnover:            ₹{t['buy_turnover']:>15,.2f}",
            f"  - Sell Turnover:           ₹{t['sell_turnover']:>15,.2f}",
            "--------------------------------------------------------------------------------",
            "ITEMISED ORDINARY TRANSACTION CHARGES (NSE Cash Delivery):",
            f"  - Brokerage (0.0%):        ₹{o['brokerage']:>15,.2f}",
            f"  - STT (0.10% Buy & Sell):  ₹{o['stt']:>15,.2f}",
            f"  - NSE Txn Fee (0.00307%):  ₹{o['nse_txn']:>15,.2f}",
            f"  - SEBI Fee (₹10/crore):    ₹{o['sebi_fee']:>15,.2f}",
            f"  - Stamp Duty (0.015% Buy): ₹{o['stamp_duty']:>15,.2f}",
            f"  - GST (18% on Base Fees):  ₹{o['gst']:>15,.2f}",
            f"  Total Ordinary Charges:    ₹{o['total_ordinary']:>15,.2f}",
            "--------------------------------------------------------------------------------",
            "DEPOSITORY PARTICIPANT (DP) CHARGES:",
            f"  - Schedule: ₹9.50 broker + ₹3.50 CDSL/NSDL + 18% GST = ₹{dp['rate_per_isin_day']:.2f}/day",
            f"  - Qualifying ISIN-Days:    {dp['qualifying_isin_days_count']:>16}",
            f"  Total DP Charges:          ₹{dp['total_dp_charges']:>15,.2f}",
            "--------------------------------------------------------------------------------",
            "NAUTILUS COMMISSION TOTAL (Debited authoritatively from Cash Balance):",
            f"  Total Commission:          ₹{sm['total_commission_deducted']:>15,.2f}",
            "--------------------------------------------------------------------------------",
            "EXECUTION SLIPPAGE (Adverse Tick Increments from Actual Fills):",
            f"  - Configured Slippage:     {s['slippage_rate_pct'] * 100:>14.3f}% per side",
            f"  - Actual Slipped Fills:    {s['slipped_fill_count']:>16}",
            f"  Total Slipped Price Drag:  ₹{s['total_slippage_cost']:>15,.2f}",
            "================================================================================",
            "COMBINED TOTAL EXECUTION DRAG:",
            f"  Nautilus Commissions:      ₹{sm['total_commission_deducted']:>15,.2f}",
            f"  Execution Slippage:        ₹{sm['total_slippage_drag']:>15,.2f}",
            f"  GRAND TOTAL FRICTION:      ₹{sm['grand_total_execution_drag']:>15,.2f} ({sm['drag_basis_points']:.2f} bps)",
            "================================================================================",
        ]
        return "\n".join(lines)
