import logging
import pathlib
from typing import Any, Dict, Optional, Union
import pandas as pd

from equity_strategy.execution.cost_reports import ExecutionCostReport
from equity_strategy.execution.fee_model import IndianEquityDeliveryFeeModel
from equity_strategy.execution.recorder import ExecutionEventRecorder

logger = logging.getLogger(__name__)


class CostExporter:
    """
    Exports execution cost breakdowns, DP charges, adverse slippage logs,
    and formatted text audit reports to dedicated run artifacts.
    """

    @classmethod
    def export(
        cls,
        cost_report: Optional[ExecutionCostReport],
        fee_model: Optional[IndianEquityDeliveryFeeModel],
        recorder: Optional[ExecutionEventRecorder],
        run_dir: Union[str, pathlib.Path],
    ) -> Dict[str, str]:
        """
        Saves cost_report.txt and execution_costs.csv to run_dir.

        Returns a dictionary mapping artifact name to its saved path.
        """
        out_dir = pathlib.Path(run_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        saved_files: Dict[str, str] = {}

        # 1. Cost Report Text
        try:
            txt_path = out_dir / "cost_report.txt"
            if cost_report is not None:
                report_txt = cost_report.to_text()
            else:
                report_txt = "No execution cost report available for this run."
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(report_txt)
            saved_files["cost_report.txt"] = str(txt_path)
            logger.info("Exported cost report text: %s", txt_path)
        except Exception as exc:
            logger.error("Failed to export cost report text: %s", exc)

        # 2. Execution Costs CSV
        try:
            fee_df = (
                fee_model.to_dataframe()
                if fee_model is not None and hasattr(fee_model, "to_dataframe")
                else pd.DataFrame()
            )
            slip_df = (
                recorder.to_dataframe()
                if recorder is not None and hasattr(recorder, "to_dataframe")
                else pd.DataFrame()
            )

            if not fee_df.empty and not slip_df.empty and "trade_id" in fee_df.columns and "trade_id" in slip_df.columns:
                # Merge on trade_id
                cols_to_merge = [c for c in ["trade_id", "reference_price", "slippage_per_share", "total_slippage_cost"] if c in slip_df.columns]
                merged_df = pd.merge(fee_df, slip_df[cols_to_merge], on="trade_id", how="left")
                if "total_commission" in merged_df.columns and "total_slippage_cost" in merged_df.columns:
                    merged_df["total_execution_drag"] = (
                        merged_df["total_commission"].fillna(0.0) + merged_df["total_slippage_cost"].fillna(0.0)
                    ).round(2)
                cost_df = merged_df
            elif not fee_df.empty:
                cost_df = fee_df.copy()
                cost_df["reference_price"] = cost_df["price"] if "price" in cost_df.columns else 0.0
                cost_df["slippage_per_share"] = 0.0
                cost_df["total_slippage_cost"] = 0.0
                if "total_commission" in cost_df.columns:
                    cost_df["total_execution_drag"] = cost_df["total_commission"]
            elif not slip_df.empty:
                cost_df = slip_df.copy()
            else:
                # Standard schema empty dataframe
                cost_df = pd.DataFrame(columns=[
                    "trade_id", "client_order_id", "symbol", "date", "is_buy",
                    "quantity", "price", "reference_price", "turnover",
                    "brokerage", "stt", "nse_txn", "sebi_fee", "stamp_duty",
                    "gst", "total_ordinary", "dp_charge", "total_commission",
                    "slippage_per_share", "total_slippage_cost", "total_execution_drag",
                ])

            csv_path = out_dir / "execution_costs.csv"
            cost_df.to_csv(csv_path, index=False)
            saved_files["execution_costs.csv"] = str(csv_path)
            logger.info("Exported execution costs CSV: %s (%d rows)", csv_path, len(cost_df))
        except Exception as exc:
            logger.error("Failed to export execution costs CSV: %s", exc)

        return saved_files
