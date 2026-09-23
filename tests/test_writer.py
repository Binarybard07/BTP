import pathlib
import pytest
import pandas as pd
import numpy as np

from equity_strategy.features.engine import FeatureEngine
from equity_strategy.features.writer import FeatureDatasetWriter

@pytest.fixture
def sample_5_stock_raw_data(tmp_path):
    """
    Creates a temporary raw folder containing 5 deterministic stock CSV files.
    """
    raw_dir = tmp_path / "prices_5_stocks"
    raw_dir.mkdir()

    symbols = ["STOCK_A", "STOCK_B", "STOCK_C", "STOCK_D", "STOCK_E"]
    dates = pd.date_range("2023-01-01", periods=100, freq="B")

    dfs = []
    for idx, sym in enumerate(symbols):
        records = []
        base_price = 100.0 + idx * 50.0
        for d_idx, dt in enumerate(dates):
            p = base_price + d_idx * 0.5
            records.append({
                "Date": dt.strftime("%Y-%m-%d"),
                "Symbol": sym,
                "Open": round(p, 2),
                "High": round(p + 2.0, 2),
                "Low": round(p - 1.0, 2),
                "Close": round(p + 1.0, 2),
                "Volume": 1000 + d_idx * 10,
            })
        df_sym = pd.DataFrame(records)
        csv_file = raw_dir / f"{sym}.csv"
        df_sym.to_csv(csv_file, index=False)
        dfs.append(df_sym)

    combined_df = pd.concat(dfs, ignore_index=True)
    combined_df["Date"] = pd.to_datetime(combined_df["Date"])
    return raw_dir, combined_df

def test_feature_dataset_writer_verifications(sample_5_stock_raw_data):
    raw_dir, combined_df = sample_5_stock_raw_data

    # Record initial raw file contents to verify immutability
    raw_file_contents = {f: f.read_text() for f in raw_dir.glob("*.csv")}

    # 1. Transform raw data using FeatureEngine
    engine = FeatureEngine()
    df_featured = engine.transform(combined_df)

    # 2. Write feature dataset via FeatureDatasetWriter
    writer = FeatureDatasetWriter()
    written_files = writer.write(raw_dir, df_featured)

    # Check 1: Correct {old_folder}_features directory is created
    expected_feature_dir = raw_dir.parent / f"{raw_dir.name}_features"
    assert expected_feature_dir.exists() and expected_feature_dir.is_dir()
    assert writer.get_feature_directory(raw_dir) == expected_feature_dir

    # Check 2: Correct {symbol}_feature.csv filenames are generated
    expected_symbols = ["STOCK_A", "STOCK_B", "STOCK_C", "STOCK_D", "STOCK_E"]
    for sym in expected_symbols:
        expected_file = expected_feature_dir / f"{sym}_feature.csv"
        assert expected_file.exists() and expected_file.is_file()

    assert len(written_files) == 5

    # Check 3 & 4: Date, Symbol, OHLCV, and all dynamic FeatureEngine columns are retained
    sample_file = expected_feature_dir / "STOCK_A_feature.csv"
    saved_df = pd.read_csv(sample_file)

    for col in df_featured.columns:
        assert col in saved_df.columns, f"Column '{col}' missing from saved feature CSV."

    assert list(saved_df.columns[:7]) == ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume"]

    # Check 5: NaNs are preserved for insufficient history
    assert saved_df["return_21"].iloc[:20].isna().all()
    assert not saved_df["return_21"].iloc[21:].isna().all()

    # Check 6: Overwrites existing files on second run without duplicating or appending rows
    initial_row_count = len(saved_df)
    writer.write(raw_dir, df_featured)
    saved_df_second_run = pd.read_csv(sample_file)
    assert len(saved_df_second_run) == initial_row_count

    # Check 7: Original raw file remains completely unchanged
    for f, original_text in raw_file_contents.items():
        assert f.read_text() == original_text

def test_feature_dataset_writer_with_single_file(sample_5_stock_raw_data):
    raw_dir, combined_df = sample_5_stock_raw_data
    single_raw_file = raw_dir / "STOCK_A.csv"

    engine = FeatureEngine()
    df_stock_a = engine.transform(combined_df[combined_df["Symbol"] == "STOCK_A"])

    writer = FeatureDatasetWriter()
    written = writer.write(single_raw_file, df_stock_a)

    expected_feature_dir = raw_dir.parent / f"{raw_dir.name}_features"
    expected_file = expected_feature_dir / "STOCK_A_feature.csv"

    assert expected_feature_dir.exists()
    assert expected_file.exists()
    assert written == [expected_file]
