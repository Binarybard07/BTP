import pytest
import pandas as pd
import numpy as np

from equity_strategy.signals.baseline import BaselineSignal

def test_baseline_signal_ranking():
    # 3 stocks on a single date with known feature values
    df = pd.DataFrame({
        "Date": [pd.Timestamp("2024-01-02")] * 3,
        "Symbol": ["STOCK_A", "STOCK_B", "STOCK_C"],
        "return_21": [0.10, 0.20, 0.05],     # Ranks: A=2/3, B=3/3, C=1/3
        "return_63": [0.15, 0.30, 0.10],     # Ranks: A=2/3, B=3/3, C=1/3
        "close_ma50": [1.05, 1.10, 1.02],    # Ranks: A=2/3, B=3/3, C=1/3
        "close_ma200": [1.10, 1.20, 1.05],   # Ranks: A=2/3, B=3/3, C=1/3
    })

    signal_model = BaselineSignal()
    result = signal_model.generate_signals(df)

    # STOCK_B has highest values across all features => rank = 1.0 for all features => FinalScore = 1.0
    stock_b_score = result[result["Symbol"] == "STOCK_B"]["FinalScore"].iloc[0]
    stock_c_score = result[result["Symbol"] == "STOCK_C"]["FinalScore"].iloc[0]

    assert np.isclose(stock_b_score, 1.0)
    assert stock_b_score > stock_c_score

def test_baseline_signal_missing_feature_exclusion():
    # STOCK_A missing return_21 => excluded
    df = pd.DataFrame({
        "Date": [pd.Timestamp("2024-01-02")] * 2,
        "Symbol": ["STOCK_A", "STOCK_B"],
        "return_21": [np.nan, 0.20],
        "return_63": [0.15, 0.30],
        "close_ma50": [1.05, 1.10],
        "close_ma200": [1.10, 1.20],
    })

    signal_model = BaselineSignal()
    result = signal_model.generate_signals(df)

    stock_a_res = result[result["Symbol"] == "STOCK_A"]["FinalScore"].iloc[0]
    stock_b_res = result[result["Symbol"] == "STOCK_B"]["FinalScore"].iloc[0]

    assert np.isnan(stock_a_res)
    assert not np.isnan(stock_b_res)
