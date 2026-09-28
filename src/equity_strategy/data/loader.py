import logging
import pathlib
from typing import Dict, List, Optional, Tuple, Union
import pandas as pd

from equity_strategy.data.validation import DataValidationError, DataValidator

logger = logging.getLogger(__name__)


class CSVDataLoader:
    """
    Recursively discovers, loads, standardizes, validates, and combines raw OHLCV market data CSVs.
    """

    REQUIRED_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume", "Symbol"]

    def __init__(
        self,
        data_dir: Union[str, pathlib.Path],
        validator: Optional[DataValidator] = None,
    ):
        self.data_dir = pathlib.Path(data_dir)
        self.validator = validator or DataValidator()
        self.skipped_files: List[Tuple[str, str]] = []
        self.invalid_files: Dict[str, str] = {}

    def discover_files(self) -> List[pathlib.Path]:
        """Discover all CSV files recursively under data_dir."""
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Data directory '{self.data_dir}' does not exist.")

        if self.data_dir.is_file() and self.data_dir.suffix.lower() == ".csv":
            return [self.data_dir]

        csv_files = sorted(list(self.data_dir.rglob("*.csv")))
        if not csv_files:
            raise FileNotFoundError(f"No CSV files found in '{self.data_dir}'.")
        return csv_files

    def _report_invalid_file(self, filename: str, issue: str) -> None:
        """
        Records and reports an invalid file along with its exact validation issue.
        """
        self.skipped_files.append((filename, issue))
        self.invalid_files[filename] = issue
        logger.warning("[DATA VALIDATION SKIP] File: '%s' -> Issue: %s", filename, issue)

    def load_file(
        self,
        file_path: Union[str, pathlib.Path],
        validate: bool = True,
    ) -> pd.DataFrame:
        """
        Loads, standardizes, and strictly validates a single CSV file independently.

        Raises:
            DataValidationError: If the file fails schema, parsing, or domain validation checks.
            ValueError: If an attempt is made to bypass validation via validate=False.
        """
        if not validate:
            raise ValueError(
                "Validation cannot be disabled. CSVDataLoader always enforces strict validation."
            )

        p = pathlib.Path(file_path)
        try:
            df = pd.read_csv(p)
        except Exception as e:
            raise DataValidationError(f"Failed to read CSV file: {e}") from e

        if df.empty:
            raise DataValidationError("CSV file is empty.")

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
            raise DataValidationError(f"Missing required columns: {missing}")

        df = df[self.REQUIRED_COLUMNS].copy()

        # Parse Date
        try:
            df["Date"] = pd.to_datetime(df["Date"], format="mixed", dayfirst=True)
        except Exception as e:
            raise DataValidationError(f"Failed to parse 'Date' column: {e}") from e

        # Preserve actual NaN / empty values so DataValidator can detect them;
        # do not convert missing Symbol values into the string "NAN".
        df["Symbol"] = df["Symbol"].apply(
            lambda x: str(x).strip().upper() if pd.notna(x) else x
        )

        # Numeric conversions
        numeric_cols = ["Open", "High", "Low", "Close", "Volume"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Strict validation is always enforced independently
        self.validator.validate(df)

        # Sort individual file by Symbol and Date
        df = df.sort_values(by=["Symbol", "Date"]).reset_index(drop=True)
        return df

    def load_data(self, validate: bool = True) -> pd.DataFrame:
        """
        Load all discovered CSV files, validate each CSV independently before combining,
        skip invalid files while reporting exact issues, and concatenate only valid files.
        Validation is always strictly enforced.

        Raises:
            ValueError: If an attempt is made to bypass validation via validate=False,
                        or if no discovered files pass validation.

        Returns:
            pd.DataFrame: Combined DataFrame containing only valid files, sorted by Symbol and Date.
        """
        if not validate:
            raise ValueError(
                "Validation cannot be disabled. CSVDataLoader always enforces strict validation."
            )

        csv_files = self.discover_files()
        valid_dfs: List[pd.DataFrame] = []
        self.skipped_files = []
        self.invalid_files = {}

        for file_path in csv_files:
            filename = file_path.name
            try:
                df = self.load_file(file_path, validate=True)
                valid_dfs.append(df)
            except Exception as exc:
                err_msg = str(exc)
                self._report_invalid_file(filename, err_msg)
                continue

        if not valid_dfs:
            raise ValueError(
                f"No valid data files passed validation across discovered files in '{self.data_dir}'."
            )

        # Only concatenate files that passed validation
        combined = pd.concat(valid_dfs, ignore_index=True)

        # Final sort by Symbol and Date
        combined = combined.sort_values(by=["Symbol", "Date"]).reset_index(drop=True)

        return combined