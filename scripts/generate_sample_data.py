import os
import pathlib
import logging
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def generate_synthetic_data(
    num_symbols: int = 100,
    num_trading_days: int = 800,
    start_date: str = "2021-01-01",
    output_dir: str = "data/sample"
) -> pathlib.Path:
    """
    Generates synthetic daily OHLCV market data for testing research & backtesting pipelines.
    Saved to output_dir (defaults to data/sample).
    """
    np.random.seed(42)

    out_path = pathlib.Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Generate business dates
    date_range = pd.date_range(start=start_date, periods=num_trading_days, freq="B")

    # Indian equity style symbol names
    symbols = [f"STOCK_{i+1:03d}" for i in range(num_symbols)]

    records = []

    logger.info(f"Generating synthetic OHLCV data for {num_symbols} stocks over {len(date_range)} sessions...")

    for sym in symbols:
        # Initial stock price between 100 and 2000
        start_price = np.random.uniform(100.0, 2000.0)
        daily_drift = np.random.normal(0.0003, 0.0001)
        daily_vol = np.random.uniform(0.012, 0.025)

        # Geometric Brownian Motion simulation
        random_returns = np.random.normal(daily_drift, daily_vol, size=len(date_range))
        price_series = start_price * np.exp(np.cumsum(random_returns))

        for dt, close_p in zip(date_range, price_series):
            # Generate intra-day High, Low, Open relative to Close
            day_vol = np.random.uniform(0.005, 0.02)
            open_p = close_p * np.random.uniform(1.0 - day_vol, 1.0 + day_vol)
            high_p = max(open_p, close_p) * np.random.uniform(1.0, 1.0 + day_vol)
            low_p = min(open_p, close_p) * np.random.uniform(1.0 - day_vol, 1.0)

            # Ensure valid OHLC bounds
            high_p = max(high_p, open_p, close_p)
            low_p = min(low_p, open_p, close_p)

            volume = int(np.random.uniform(50_000, 1_500_000))

            records.append({
                "Date": dt.strftime("%Y-%m-%d"),
                "Symbol": sym,
                "Open": round(open_p, 2),
                "High": round(high_p, 2),
                "Low": round(low_p, 2),
                "Close": round(close_p, 2),
                "Volume": volume,
            })

    df = pd.DataFrame(records)
    df = df.sort_values(by=["Symbol", "Date"]).reset_index(drop=True)

    csv_file = out_path / "synthetic_prices_2021_2024.csv"
    df.to_csv(csv_file, index=False)

    logger.info(f"Successfully generated {len(df)} OHLCV rows across {num_symbols} symbols. Saved to '{csv_file}'.")
    return csv_file

if __name__ == "__main__":
    generate_synthetic_data()
