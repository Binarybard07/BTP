import pytest
import pandas as pd
import numpy as np

from equity_strategy.data.loader import CSVDataLoader
from equity_strategy.data.validation import DataValidator, DataValidationError

def test_data_validation_valid(tmp_path):
    df = pd.DataFrame({
        "Date": ["2024-01-02", "2024-01-03"],
        "Symbol": ["RELIANCE", "RELIANCE"],
        "Open": [1000.0, 1010.0],
        "High": [1050.0, 1060.0],
        "Low": [990.0, 1000.0],
        "Close": [1040.0, 1050.0],
        "Volume": [10000, 12000],
    })

    validator = DataValidator()
    assert validator.validate(df) is True

def test_data_validation_high_less_than_low():
    df = pd.DataFrame({
        "Date": ["2024-01-02"],
        "Symbol": ["TCS"],
        "Open": [1000.0],
        "High": [900.0],  # Invalid: High < Low
        "Low": [950.0],
        "Close": [980.0],
        "Volume": [5000],
    })

    validator = DataValidator()
    with pytest.raises(DataValidationError, match="High < Low"):
        validator.validate(df)

def test_data_validation_duplicate_rows():
    df = pd.DataFrame({
        "Date": ["2024-01-02", "2024-01-02"],
        "Symbol": ["INFY", "INFY"],  # Duplicate Date+Symbol
        "Open": [1500.0, 1500.0],
        "High": [1550.0, 1550.0],
        "Low": [1490.0, 1490.0],
        "Close": [1530.0, 1530.0],
        "Volume": [8000, 8000],
    })

    validator = DataValidator()
    with pytest.raises(DataValidationError, match="duplicate"):
        validator.validate(df)

def test_csv_loader_recursive_discovery(tmp_path):
    sub_dir = tmp_path / "subdir"
    sub_dir.mkdir()

    file1 = tmp_path / "prices1.csv"
    file2 = sub_dir / "prices2.csv"

    content1 = "Date,Symbol,Open,High,Low,Close,Volume\n2024-01-02,STOCK_A,100,105,95,102,5000\n"
    content2 = "Date,Symbol,Open,High,Low,Close,Volume\n2024-01-02,STOCK_B,200,208,195,205,6000\n"

    file1.write_text(content1)
    file2.write_text(content2)

    loader = CSVDataLoader(tmp_path)
    df = loader.load_data(validate=True)

    assert len(df) == 2
    assert set(df["Symbol"]) == {"STOCK_A", "STOCK_B"}
