import pandas as pd
import pytest

from nautilus_trader.model import Bar, BarType, Equity, InstrumentId

from equity_strategy.data.nautilus_data import NautilusDataConverter


@pytest.fixture
def converter():
    return NautilusDataConverter()


@pytest.fixture
def sample_ohlcv_df():
    return pd.DataFrame({
        "Date": ["2024-01-01", "2024-01-02", "2024-01-03"],
        "Open": [100.0, 102.5, 101.0],
        "High": [105.0, 106.0, 103.5],
        "Low": [99.0, 101.0, 98.5],
        "Close": [103.0, 104.5, 99.5],
        "Volume": [50000, 62000, 48000],
        "Symbol": ["ABB", "ABB", "ABB"],
    })


def test_create_equity(converter):
    equity = converter.create_equity(symbol="ABB", venue="NSE", currency="INR")
    assert isinstance(equity, Equity)
    assert str(equity.id) == "ABB.NSE"
    assert str(equity.raw_symbol) == "ABB"
    assert str(equity.quote_currency) == "INR"
    assert equity.price_precision == 2


def test_create_bar_type(converter):
    inst_id = InstrumentId.from_str("ABB.NSE")
    bar_type = converter.create_bar_type(inst_id)
    assert isinstance(bar_type, BarType)
    assert str(bar_type) == "ABB.NSE-1-DAY-LAST-EXTERNAL"
    assert str(bar_type.instrument_id) == "ABB.NSE"


def test_timestamp_to_ns(converter):
    # Daily string date without time -> localized to 15:30 IST -> 10:00 UTC
    ns = converter.timestamp_to_ns("2024-01-01")
    expected_utc = pd.Timestamp("2024-01-01 10:00:00", tz="UTC").value
    assert ns == expected_utc


def test_df_to_bars(converter, sample_ohlcv_df):
    bar_type = converter.create_bar_type("ABB.NSE")
    bars = converter.df_to_bars(sample_ohlcv_df, bar_type=bar_type)

    assert len(bars) == 3
    first_bar = bars[0]
    assert isinstance(first_bar, Bar)
    assert first_bar.bar_type == bar_type
    assert float(first_bar.open) == 100.0
    assert float(first_bar.high) == 105.0
    assert float(first_bar.low) == 99.0
    assert float(first_bar.close) == 103.0
    assert int(first_bar.volume) == 50000


def test_convert_dataframe_multi_symbol(converter):
    df_multi = pd.DataFrame({
        "Date": ["2024-01-01", "2024-01-01"],
        "Open": [100.0, 200.0],
        "High": [105.0, 205.0],
        "Low": [99.0, 195.0],
        "Close": [103.0, 202.0],
        "Volume": [1000, 2000],
        "Symbol": ["ABB", "BEL"],
    })

    result = converter.convert_dataframe(df_multi)
    assert "ABB" in result
    assert "BEL" in result

    abb_equity, abb_bar_type, abb_bars = result["ABB"]
    assert str(abb_equity.id) == "ABB.NSE"
    assert len(abb_bars) == 1
    assert float(abb_bars[0].close) == 103.0

    bel_equity, bel_bar_type, bel_bars = result["BEL"]
    assert str(bel_equity.id) == "BEL.NSE"
    assert len(bel_bars) == 1
    assert float(bel_bars[0].close) == 202.0