import pandas as pd
import pytest

from nautilus_trader.model import Bar, BarType, Equity

from equity_strategy.data.strategy_data import PreparedStrategyData, StrategyDataPipeline


@pytest.fixture
def sample_ohlcv_df():
    return pd.DataFrame({
        "Date": [
            "2024-01-01",
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
        ],
        "Open": [100.0, 102.0, 101.0, 103.0, 105.0],
        "High": [105.0, 106.0, 104.0, 107.0, 108.0],
        "Low": [99.0, 100.0, 99.0, 102.0, 104.0],
        "Close": [103.0, 104.0, 102.0, 106.0, 107.0],
        "Volume": [10000, 12000, 11000, 15000, 14000],
        "Symbol": ["TCS", "TCS", "TCS", "TCS", "TCS"],
    })


def test_strategy_data_pipeline_prepare_from_dataframe(sample_ohlcv_df):
    pipeline = StrategyDataPipeline()
    prepared = pipeline.prepare(sample_ohlcv_df, symbol="TCS", venue="NSE")

    assert isinstance(prepared, PreparedStrategyData)
    assert isinstance(prepared.equity, Equity)
    assert str(prepared.equity.id) == "TCS.NSE"
    assert isinstance(prepared.bar_type, BarType)
    assert str(prepared.bar_type) == "TCS.NSE-1-DAY-LAST-EXTERNAL"
    assert len(prepared.bars) == 5
    assert isinstance(prepared.bars[0], Bar)

    # Check feature computation
    assert "close_to_ma50" in prepared.featured_df.columns
    assert "close_to_ma200" in prepared.featured_df.columns
    assert "return_21" in prepared.featured_df.columns
    assert "volatility_21" in prepared.featured_df.columns

    # Check tuple unpacking
    df_feat, equity, bar_type, bars = prepared
    assert df_feat is prepared.featured_df
    assert equity is prepared.equity
    assert bar_type is prepared.bar_type
    assert bars is prepared.bars


def test_strategy_data_pipeline_prepare_from_csv(tmp_path, sample_ohlcv_df):
    csv_file = tmp_path / "TCS.csv"
    sample_ohlcv_df.to_csv(csv_file, index=False)

    pipeline = StrategyDataPipeline()
    prepared = pipeline.prepare(csv_file)

    assert str(prepared.equity.id) == "TCS.NSE"
    assert len(prepared.bars) == 5
    assert float(prepared.bars[-1].close) == 107.0


def test_strategy_data_pipeline_missing_symbol(sample_ohlcv_df):
    pipeline = StrategyDataPipeline()
    with pytest.raises(ValueError, match="Symbol 'INFY' not found"):
        pipeline.prepare(sample_ohlcv_df, symbol="INFY")
