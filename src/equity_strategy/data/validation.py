from typing import List
import pandas as pd

class DataValidationError(Exception):
    """Raised when market data fails validation constraints."""
    pass

class DataValidator:
    """
    Validates market data schemas, numeric ranges, price relationships, and uniqueness constraints.
    """

    REQUIRED_COLUMNS = ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume"]

    def validate(self, df: pd.DataFrame) -> bool:
        """
        Runs validation checks on the given DataFrame. Raises DataValidationError if any check fails.
        """
        if df.empty:
            raise DataValidationError("DataFrame is empty.")

        # Check required columns
        missing_cols = [c for c in self.REQUIRED_COLUMNS if c not in df.columns]
        if missing_cols:
            raise DataValidationError(f"Missing required columns: {missing_cols}")

        # Check non-null Date and Symbol
        if df["Date"].isna().any():
            raise DataValidationError("Found null values in 'Date' column.")

        if df["Symbol"].isna().any() or (df["Symbol"] == "").any():
            raise DataValidationError("Found null or empty values in 'Symbol' column.")

        # Check numeric nulls
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if df[col].isna().any():
                raise DataValidationError(f"Found null or unparseable values in '{col}' column.")

        # Positive price checks
        if (df["Open"] <= 0).any():
            raise DataValidationError("Found Open price <= 0.")
        if (df["High"] <= 0).any():
            raise DataValidationError("Found High price <= 0.")
        if (df["Low"] <= 0).any():
            raise DataValidationError("Found Low price <= 0.")
        if (df["Close"] <= 0).any():
            raise DataValidationError("Found Close price <= 0.")

        # Non-negative volume check
        if (df["Volume"] < 0).any():
            raise DataValidationError("Found Volume < 0.")

        # Price relationship checks
        if (df["High"] < df["Low"]).any():
            invalid = df[df["High"] < df["Low"]]
            raise DataValidationError(f"Found High < Low in {len(invalid)} rows.")

        if (df["High"] < df["Open"]).any():
            invalid = df[df["High"] < df["Open"]]
            raise DataValidationError(f"Found High < Open in {len(invalid)} rows.")

        if (df["High"] < df["Close"]).any():
            invalid = df[df["High"] < df["Close"]]
            raise DataValidationError(f"Found High < Close in {len(invalid)} rows.")

        if (df["Low"] > df["Open"]).any():
            invalid = df[df["Low"] > df["Open"]]
            raise DataValidationError(f"Found Low > Open in {len(invalid)} rows.")

        if (df["Low"] > df["Close"]).any():
            invalid = df[df["Low"] > df["Close"]]
            raise DataValidationError(f"Found Low > Close in {len(invalid)} rows.")

        # Unique Date + Symbol check
        duplicates = df.duplicated(subset=["Date", "Symbol"], keep=False)
        if duplicates.any():
            dups = df[duplicates].sort_values(by=["Symbol", "Date"])
            sample_dups = dups[["Date", "Symbol"]].head(4).to_dict(orient="records")
            raise DataValidationError(f"Found duplicate (Date, Symbol) combinations: {sample_dups}")

        return True
