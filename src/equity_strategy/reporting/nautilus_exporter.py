import logging
import pathlib
from typing import Any, Dict, List, Optional, Union
import pandas as pd

from nautilus_trader.analysis import create_tearsheet
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.model import Currency, Venue

logger = logging.getLogger(__name__)


class NautilusExporter:
    """
    Exports native NautilusTrader reports, statistics, returns series,
    and HTML tearsheets to dedicated run artifacts.

    Preserves full column sets from NautilusTrader native reports without truncating schema.
    """

    @classmethod
    def export(
        cls,
        engine: BacktestEngine,
        run_dir: Union[str, pathlib.Path],
        venue: str = "NSE",
        currency: str = "INR",
        generate_tearsheet: bool = True,
    ) -> Dict[str, str]:
        """
        Extracts and saves all NautilusTrader backtest reports to run_dir.

        Returns a dictionary mapping artifact name to its saved path.
        """
        out_dir = pathlib.Path(run_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        saved_files: Dict[str, str] = {}

        # 1. Orders Report
        try:
            orders_df = engine.generate_orders_report()
            path = out_dir / "orders.csv"
            if orders_df is not None and not orders_df.empty:
                orders_df.to_csv(path)
            else:
                pd.DataFrame().to_csv(path, index=False)
            saved_files["orders.csv"] = str(path)
            logger.info("Exported orders report: %s (%d rows)", path, len(orders_df) if orders_df is not None else 0)
        except Exception as exc:
            logger.error("Failed to export orders report: %s", exc)

        # 2. Order Fills Report
        try:
            order_fills_df = engine.generate_order_fills_report()
            path = out_dir / "order_fills.csv"
            if order_fills_df is not None and not order_fills_df.empty:
                order_fills_df.to_csv(path)
            else:
                pd.DataFrame().to_csv(path, index=False)
            saved_files["order_fills.csv"] = str(path)
            logger.info("Exported order fills report: %s (%d rows)", path, len(order_fills_df) if order_fills_df is not None else 0)
        except Exception as exc:
            logger.error("Failed to export order fills report: %s", exc)

        # 3. Fills Report
        try:
            fills_df = engine.generate_fills_report()
            path = out_dir / "fills.csv"
            if fills_df is not None and not fills_df.empty:
                fills_df.to_csv(path)
            else:
                pd.DataFrame().to_csv(path, index=False)
            saved_files["fills.csv"] = str(path)
            logger.info("Exported fills report: %s (%d rows)", path, len(fills_df) if fills_df is not None else 0)
        except Exception as exc:
            logger.error("Failed to export fills report: %s", exc)

        # 4. Positions Report
        try:
            positions_df = engine.generate_positions_report()
            path = out_dir / "positions.csv"
            if positions_df is not None and not positions_df.empty:
                positions_df.to_csv(path)
            else:
                pd.DataFrame().to_csv(path, index=False)
            saved_files["positions.csv"] = str(path)
            logger.info("Exported positions report: %s (%d rows)", path, len(positions_df) if positions_df is not None else 0)
        except Exception as exc:
            logger.error("Failed to export positions report: %s", exc)

        # 5. Account Report
        try:
            account_df = engine.generate_account_report(venue=Venue(venue))
            path = out_dir / "account.csv"
            if account_df is not None and not account_df.empty:
                account_df.to_csv(path)
            else:
                pd.DataFrame().to_csv(path, index=False)
            saved_files["account.csv"] = str(path)
            logger.info("Exported account report: %s (%d rows)", path, len(account_df) if account_df is not None else 0)
        except Exception as exc:
            logger.error("Failed to export account report: %s", exc)

        # 6. BacktestResult Statistics & Returns Series
        try:
            result = engine.get_result()
            if result is not None:
                # 6a. stats.csv
                stats_rows: List[Dict[str, Any]] = []

                # Scalar attributes
                for attr in ("run_id", "instance_id", "trader_id", "elapsed_time_secs", "iterations", "total_events", "total_orders", "total_positions", "backtest_start", "backtest_end", "run_started", "run_finished"):
                    val = getattr(result, attr, None)
                    if val is not None:
                        stats_rows.append({"category": "execution", "metric": attr, "value": str(val)})

                # Summary dict
                summary_dict = getattr(result, "summary", None)
                if isinstance(summary_dict, dict):
                    for k, v in summary_dict.items():
                        stats_rows.append({"category": "summary", "metric": str(k), "value": v})

                # Stats categories
                for cat_name, cat_dict in (
                    ("general", getattr(result, "stats_general", None)),
                    ("returns", getattr(result, "stats_returns", None)),
                    ("pnls", getattr(result, "stats_pnls", None)),
                ):
                    if isinstance(cat_dict, dict):
                        for k, v in cat_dict.items():
                            stats_rows.append({"category": cat_name, "metric": str(k), "value": v})

                stats_df = pd.DataFrame(stats_rows)
                stats_path = out_dir / "stats.csv"
                stats_df.to_csv(stats_path, index=False)
                saved_files["stats.csv"] = str(stats_path)
                logger.info("Exported backtest statistics: %s (%d metrics)", stats_path, len(stats_df))

                # 6b. returns_series.csv
                returns = getattr(result, "returns_series", None)
                returns_rows: List[Dict[str, Any]] = []
                if isinstance(returns, dict):
                    for ts, ret in returns.items():
                        try:
                            ts_int = int(ts)
                            dt_utc = pd.to_datetime(ts_int, unit="ns", utc=True)
                            dt_ist = dt_utc.tz_convert("Asia/Kolkata")
                            returns_rows.append({
                                "timestamp_ns": ts_int,
                                "timestamp_utc": dt_utc.isoformat(),
                                "date_ist": dt_ist.strftime("%Y-%m-%d"),
                                "return": float(ret),
                            })
                        except Exception:
                            returns_rows.append({"timestamp": ts, "return": ret})
                elif isinstance(returns, (pd.Series, list)):
                    if isinstance(returns, pd.Series):
                        for ts, ret in returns.items():
                            returns_rows.append({"timestamp": str(ts), "return": float(ret)})
                    else:
                        for idx, ret in enumerate(returns):
                            returns_rows.append({"index": idx, "return": float(ret)})

                returns_df = pd.DataFrame(returns_rows)
                returns_path = out_dir / "returns_series.csv"
                returns_df.to_csv(returns_path, index=False)
                saved_files["returns_series.csv"] = str(returns_path)
                logger.info("Exported returns series: %s (%d rows)", returns_path, len(returns_df))

        except Exception as exc:
            logger.error("Failed to export BacktestResult stats/returns: %s", exc)

        # 7. Tearsheet HTML
        if generate_tearsheet:
            try:
                tearsheet_path = out_dir / "tearsheet.html"
                create_tearsheet(
                    engine=engine,
                    output_path=str(tearsheet_path),
                    title=f"NautilusTrader Backtest - {out_dir.name}",
                    currency=Currency.from_str(currency) if currency else None,
                )
                if tearsheet_path.exists():
                    saved_files["tearsheet.html"] = str(tearsheet_path)
                    logger.info("Exported tearsheet HTML: %s", tearsheet_path)
            except Exception as exc:
                logger.warning("Could not generate tearsheet HTML: %s", exc)

        return saved_files
