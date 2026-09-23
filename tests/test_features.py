import pytest
import pandas as pd
import numpy as np

from equity_strategy.features.engine import FeatureEngine
from equity_strategy.features.momentum import compute_momentum_features
from equity_strategy.features.trend import compute_trend_features
from equity_strategy.features.volatility import compute_volatility_features

def test_momentum_features_calculation():
    dates = pd.date_range("2023-01-01", periods=300, freq="B")
    prices = [100.0 + i * 0.5 for i in range(300)]
    df = pd.DataFrame({"Date": dates, "Symbol": "TEST", "Close": prices})

    df_mom = compute_momentum_features(df, 21, 63, 252)

    # return_21 at t=21: prices[21]/prices[0] - 1
    expected_ret_21 = prices[21] / prices[0] - 1.0
    assert np.isclose(df_mom["return_21"].iloc[21], expected_ret_21)

    # First 21 rows should be NaN for return_21
    assert df_mom["return_21"].iloc[:21].isna().all()

def test_trend_features_calculation():
    dates = pd.date_range("2023-01-01", periods=250, freq="B")
    prices = [100.0] * 250
    df = pd.DataFrame({"Date": dates, "Symbol": "TEST", "Close": prices})

    df_trend = compute_trend_features(df, short_ma=50, long_ma=200)

    ma50_col = "close_to_ma50" if "close_to_ma50" in df_trend.columns else "close_ma50"
    ma200_col = "close_to_ma200" if "close_to_ma200" in df_trend.columns else "close_ma200"

    # Constant price => Close / MA = 1.0 after window
    assert np.isclose(df_trend[ma50_col].iloc[50], 1.0)
    assert np.isclose(df_trend[ma200_col].iloc[200], 1.0)
    assert df_trend[ma50_col].iloc[:49].isna().all()


def test_symbol_data_isolation():
    """Verify that features are computed independently per symbol without cross-contamination."""
    dates = pd.date_range("2023-01-01", periods=100, freq="B")

    df_sym1 = pd.DataFrame({"Date": dates, "Symbol": "SYM1", "Close": [100.0 + i for i in range(100)]})
    df_sym2 = pd.DataFrame({"Date": dates, "Symbol": "SYM2", "Close": [1000.0 - i * 2 for i in range(100)]})

    combined = pd.concat([df_sym1, df_sym2], ignore_index=True)

    engine = FeatureEngine()
    transformed = engine.transform(combined)

    sym1_res = transformed[transformed["Symbol"] == "SYM1"].sort_values(by="Date").reset_index(drop=True)
    sym2_res = transformed[transformed["Symbol"] == "SYM2"].sort_values(by="Date").reset_index(drop=True)

    # SYM1 return_21 at t=21
    exp_sym1 = (100.0 + 21) / 100.0 - 1.0
    assert np.isclose(sym1_res["return_21"].iloc[21], exp_sym1)

    # SYM2 return_21 at t=21
    exp_sym2 = (1000.0 - 21 * 2) / 1000.0 - 1.0
    assert np.isclose(sym2_res["return_21"].iloc[21], exp_sym2)
