import logging
import pathlib
from typing import Any, Dict, List, Optional, Union
import pandas as pd

logger = logging.getLogger(__name__)


class StrategyExporter:
    """
    Exports strategy ML decisions, stock selections, target portfolio weights,
    and rebalance event logs to dedicated run artifacts.
    """

    @classmethod
    def export(
        cls,
        strategy: Any,
        run_dir: Union[str, pathlib.Path],
    ) -> Dict[str, str]:
        """
        Extracts decision data from the strategy instance and saves CSV artifacts to run_dir.

        Returns a dictionary mapping artifact name to its saved path.
        """
        out_dir = pathlib.Path(run_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        saved_files: Dict[str, str] = {}

        # 1. Predictions CSV
        try:
            predictions_by_date: Dict[pd.Timestamp, pd.DataFrame] = getattr(
                strategy, "predictions_by_date", {}
            )
            pred_rows: List[Dict[str, Any]] = []

            for dt, df in predictions_by_date.items():
                if df is None or df.empty:
                    continue
                date_str = pd.to_datetime(dt).strftime("%Y-%m-%d")
                df_sorted = df.copy()
                if "score" in df_sorted.columns:
                    df_sorted = df_sorted.sort_values(by="score", ascending=False).reset_index(drop=True)

                for rank_idx, row in df_sorted.iterrows():
                    sym = str(row.get("Symbol", ""))
                    score = float(row.get("score", 0.0))
                    pred_rows.append({
                        "date": date_str,
                        "symbol": sym,
                        "predicted_score": score,
                        "rank": rank_idx + 1,
                    })

            pred_df = pd.DataFrame(pred_rows)
            path = out_dir / "predictions.csv"
            pred_df.to_csv(path, index=False)
            saved_files["predictions.csv"] = str(path)
            logger.info("Exported predictions: %s (%d rows)", path, len(pred_df))
        except Exception as exc:
            logger.error("Failed to export predictions: %s", exc)

        # 2. Selected Stocks CSV
        try:
            selected_by_date: Dict[pd.Timestamp, List[str]] = getattr(
                strategy, "selected_stocks_by_date", {}
            )
            sel_rows: List[Dict[str, Any]] = []

            for dt, symbols in selected_by_date.items():
                date_str = pd.to_datetime(dt).strftime("%Y-%m-%d")
                for rank_idx, sym in enumerate(symbols):
                    sel_rows.append({
                        "date": date_str,
                        "symbol": str(sym).strip().upper(),
                        "rank": rank_idx + 1,
                    })

            sel_df = pd.DataFrame(sel_rows)
            path = out_dir / "selected_stocks.csv"
            sel_df.to_csv(path, index=False)
            saved_files["selected_stocks.csv"] = str(path)
            logger.info("Exported selected stocks: %s (%d rows)", path, len(sel_df))
        except Exception as exc:
            logger.error("Failed to export selected stocks: %s", exc)

        # 3. Target Weights CSV
        try:
            weights_by_date: Dict[pd.Timestamp, Dict[str, float]] = getattr(
                strategy, "weights_by_date", {}
            )
            weight_rows: List[Dict[str, Any]] = []

            for dt, weights in weights_by_date.items():
                date_str = pd.to_datetime(dt).strftime("%Y-%m-%d")
                for sym, w in weights.items():
                    weight_rows.append({
                        "date": date_str,
                        "symbol": str(sym).strip().upper(),
                        "target_weight": float(w),
                    })

            weight_df = pd.DataFrame(weight_rows)
            path = out_dir / "target_weights.csv"
            weight_df.to_csv(path, index=False)
            saved_files["target_weights.csv"] = str(path)
            logger.info("Exported target weights: %s (%d rows)", path, len(weight_df))
        except Exception as exc:
            logger.error("Failed to export target weights: %s", exc)

        # 4. Rebalance History CSV
        try:
            rebal_dates: List[pd.Timestamp] = getattr(strategy, "rebalance_dates", [])
            orders_by_date: Dict[pd.Timestamp, List[Any]] = getattr(strategy, "orders_by_date", {})
            hist_rows: List[Dict[str, Any]] = []

            for dt in rebal_dates:
                date_str = pd.to_datetime(dt).strftime("%Y-%m-%d")
                selected_list = selected_by_date.get(dt, [])
                weights_dict = weights_by_date.get(dt, {})
                orders_list = orders_by_date.get(dt, [])

                # Find expected execution date if available
                date_idx = None
                exec_date_str = ""
                all_dates = getattr(strategy, "all_trading_dates", [])
                date_to_index = getattr(strategy, "date_to_index", {})
                if dt in date_to_index and date_to_index[dt] + 1 < len(all_dates):
                    exec_date_str = pd.to_datetime(all_dates[date_to_index[dt] + 1]).strftime("%Y-%m-%d")

                hist_rows.append({
                    "decision_date": date_str,
                    "execution_session": exec_date_str,
                    "num_selected": len(selected_list),
                    "num_orders": len(orders_list),
                    "total_target_weight": round(sum(weights_dict.values()), 4),
                })

            hist_df = pd.DataFrame(hist_rows)
            path = out_dir / "rebalance_history.csv"
            hist_df.to_csv(path, index=False)
            saved_files["rebalance_history.csv"] = str(path)
            logger.info("Exported rebalance history: %s (%d rebalances)", path, len(hist_df))
        except Exception as exc:
            logger.error("Failed to export rebalance history: %s", exc)

        return saved_files
