import pytest
import pandas as pd
import numpy as np

from equity_strategy.portfolio.equal_weight import EqualWeightPortfolio

def test_equal_weight_portfolio_construction():
    # 50 stocks with FinalScores from 0.01 to 0.50
    symbols = [f"STOCK_{i:02d}" for i in range(50)]
    scores = [0.01 * (i + 1) for i in range(50)]

    df_signals = pd.DataFrame({
        "Date": [pd.Timestamp("2024-01-02")] * 50,
        "Symbol": symbols,
        "FinalScore": scores,
    })

    # Default config: candidates=50, holdings=30
    config = {"portfolio": {"candidates": 50, "holdings": 30}}
    portfolio_builder = EqualWeightPortfolio(config)
    target_df = portfolio_builder.construct_portfolio(df_signals)

    # Top 30 holdings selected
    assert len(target_df) == 30
    # Equal weight = 1/30
    assert np.isclose(target_df["TargetWeight"].iloc[0], 1.0 / 30.0)
    # Sum of target weights = 1.0
    assert np.isclose(target_df["TargetWeight"].sum(), 1.0)
    # Highest score stock (STOCK_49) is rank 1
    assert target_df["Symbol"].iloc[0] == "STOCK_49"

def test_fewer_than_30_holdings():
    # Only 5 stocks available
    df_signals = pd.DataFrame({
        "Date": [pd.Timestamp("2024-01-02")] * 5,
        "Symbol": [f"STOCK_{i}" for i in range(5)],
        "FinalScore": [0.1, 0.2, 0.3, 0.4, 0.5],
    })

    portfolio_builder = EqualWeightPortfolio()
    target_df = portfolio_builder.construct_portfolio(df_signals)

    assert len(target_df) == 5
    assert np.isclose(target_df["TargetWeight"].iloc[0], 1.0 / 5.0)
    assert np.isclose(target_df["TargetWeight"].sum(), 1.0)
