import logging
import pathlib
from typing import Any, Dict, List, Optional, Union
import pandas as pd

from nautilus_trader.model import Bar, QuoteTick

logger = logging.getLogger(__name__)


class MarketDataExporter:
    """
    Exports market data (bars and synthetic opening quote ticks)
    loaded into the backtest engine to dedicated run artifacts.

    Explicitly flags opening quotes as synthetic simulator constructs.
    """

    @classmethod
    def export(
        cls,
        bars: List[Bar],
        opening_quotes: Optional[List[QuoteTick]],
        run_dir: Union[str, pathlib.Path],
        exchange_tz: str = "Asia/Kolkata",
    ) -> Dict[str, str]:
        """
        Serializes bars and opening quotes to CSV files in run_dir.

        Returns a dictionary mapping artifact name to its saved path.
        """
        out_dir = pathlib.Path(run_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        saved_files: Dict[str, str] = {}

        # 1. Bars CSV
        try:
            bar_rows: List[Dict[str, Any]] = []
            for b in bars:
                ts = int(b.ts_event)
                dt_str = (
                    pd.to_datetime(ts, unit="ns", utc=True)
                    .tz_convert(exchange_tz)
                    .strftime("%Y-%m-%d")
                )
                bar_rows.append({
                    "symbol": str(b.bar_type.instrument_id.symbol),
                    "date": dt_str,
                    "open": float(b.open.as_double()),
                    "high": float(b.high.as_double()),
                    "low": float(b.low.as_double()),
                    "close": float(b.close.as_double()),
                    "volume": float(b.volume.as_double()),
                    "ts_event": ts,
                    "ts_init": int(b.ts_init),
                    "instrument_id": str(b.bar_type.instrument_id),
                })

            bars_df = pd.DataFrame(bar_rows)
            if not bars_df.empty:
                bars_df = bars_df.sort_values(by=["symbol", "ts_event"]).reset_index(drop=True)

            path = out_dir / "bars.csv"
            bars_df.to_csv(path, index=False)
            saved_files["bars.csv"] = str(path)
            logger.info("Exported bars: %s (%d rows)", path, len(bars_df))
        except Exception as exc:
            logger.error("Failed to export bars: %s", exc)

        # 2. Opening Quotes CSV (Labeled Synthetic)
        try:
            quote_rows: List[Dict[str, Any]] = []
            if opening_quotes:
                for q in opening_quotes:
                    ts = int(q.ts_event)
                    dt_str = (
                        pd.to_datetime(ts, unit="ns", utc=True)
                        .tz_convert(exchange_tz)
                        .strftime("%Y-%m-%d")
                    )
                    quote_rows.append({
                        "symbol": str(q.instrument_id.symbol),
                        "date": dt_str,
                        "bid_price": float(q.bid_price.as_double()),
                        "ask_price": float(q.ask_price.as_double()),
                        "bid_size": float(q.bid_size.as_double()),
                        "ask_size": float(q.ask_size.as_double()),
                        "ts_event": ts,
                        "ts_init": int(q.ts_init),
                        "instrument_id": str(q.instrument_id),
                        "is_synthetic": True,
                        "quote_type": "synthetic_open",
                    })

            quotes_df = pd.DataFrame(quote_rows)
            if not quotes_df.empty:
                quotes_df = quotes_df.sort_values(by=["symbol", "ts_event"]).reset_index(drop=True)

            path = out_dir / "opening_quotes.csv"
            quotes_df.to_csv(path, index=False)
            saved_files["opening_quotes.csv"] = str(path)
            logger.info("Exported opening quotes: %s (%d rows)", path, len(quotes_df))
        except Exception as exc:
            logger.error("Failed to export opening quotes: %s", exc)

        return saved_files
