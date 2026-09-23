import pathlib
from typing import List, Union
import pandas as pd

from equity_strategy.data.validation import DataValidator

class CSVDataLoader:
    """
    Recursively discovers, loads, standardizes, sorts, and validates raw OHLCV market data CSVs.
    """

    REQUIRED_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume", "Symbol"]

    def __init__(self, data_dir: Union[str, pathlib.Path]):
        self.data_dir = pathlib.Path(data_dir)

    def discover_files(self) -> List[pathlib.Path]:
        """Discover all CSV files recursively under data_dir."""
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Data directory '{self.data_dir}' does not exist.")

        csv_files = sorted(list(self.data_dir.rglob("*.csv")))
        if not csv_files:
            raise FileNotFoundError(f"No CSV files found in '{self.data_dir}'.")
        return csv_files

    def load_data(self, validate: bool = True) -> pd.DataFrame:
        """
        Load all discovered CSV files, combine, standardize, sort, and optionally validate.
        """
        csv_files = self.discover_files()
        dfs = []

        for file_path in csv_files:
            try:
                df = pd.read_csv(file_path)
            except Exception as e:
                raise ValueError(f"Failed to read CSV file '{file_path}': {e}") from e

            if df.empty:
                continue

            # Standardize column headers: strip whitespace
            df.columns = [str(col).strip() for col in df.columns]

            # Case-insensitive column matching
            col_map = {}
            for col in df.columns:
                lower_col = col.lower()
                if lower_col == "date":
                    col_map[col] = "Date"
                elif lower_col == "symbol":
                    col_map[col] = "Symbol"
                elif lower_col == "open":
                    col_map[col] = "Open"
                elif lower_col == "high":
                    col_map[col] = "High"
                elif lower_col == "low":
                    col_map[col] = "Low"
                elif lower_col == "close":
                    col_map[col] = "Close"
                elif lower_col in ("volume", "vol"):
                    col_map[col] = "Volume"

            df = df.rename(columns=col_map)

            # Ensure all required columns exist
            missing = [c for c in self.REQUIRED_COLUMNS if c not in df.columns]
            if missing:
                raise ValueError(f"CSV file '{file_path}' is missing required columns: {missing}")

            df = df[self.REQUIRED_COLUMNS].copy()
            dfs.append(df)

        if not dfs:
            raise ValueError(f"No valid data rows found across CSV files in '{self.data_dir}'.")

        combined = pd.concat(dfs, ignore_index=True)

        # Parse Date
        combined["Date"] = pd.to_datetime(combined["Date"], format="mixed", dayfirst=True)

        combined["Symbol"] = combined["Symbol"].astype(str).str.strip().str.upper()

        # Numeric conversions
        numeric_cols = ["Open", "High", "Low", "Close", "Volume"]
        for col in numeric_cols:
            combined[col] = pd.to_numeric(combined[col], errors="coerce")

        # Sort by Symbol and Date
        combined = combined.sort_values(by=["Symbol", "Date"]).reset_index(drop=True)

        if validate:
            validator = DataValidator()
            validator.validate(combined)

        return combined
