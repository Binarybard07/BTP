import pytest
import pandas as pd
import numpy as np

from equity_strategy.research.rebalancer import MonthlyRebalancer
from equity_strategy.research.target_generator import TargetWeightGenerator
from equity_strategy.features.engine import FeatureEngine
from equity_strategy.signals.baseline import BaselineSignal

def test_monthly_rebalancer_dates():
    # 2 months of business daily trading dates
    dates = pd.date_range("2024-01-01", "2024-02-29", freq="B")
    rebalance_pairs = MonthlyRebalancer.get_rebalance_dates(dates)

    assert len(rebalance_pairs) == 1  # 1 full month end (Jan end -> Feb 1)
    signal_date, effective_date = rebalance_pairs[0]

    # Jan 31, 2024 was last trading day of Jan 2024
    assert signal_date == pd.Timestamp("2024-01-31")
    # Feb 1, 2024 was next trading day
    assert effective_date == pd.Timestamp("2024-02-01")

def test_lookahead_bias_prevention():
    """
    EXPLICIT LOOKAHEAD BIAS PREVENTION TEST:
    Modifying data AFTER date T must NOT alter the signal or ranks calculated for date T.
    """
    np.random.seed(123)
    num_symbols = 10
    dates = pd.date_range("2023-01-01", "2023-08-31", freq="B")

    records = []
    for sym_idx in range(num_symbols):
        sym = f"STOCK_{sym_idx:02d}"
        price = 100.0 + sym_idx * 10.0
        for dt in dates:
            price += np.random.normal(0.5, 1.0)
            records.append({
                "Date": dt,
                "Symbol": sym,
                "Open": price * 0.99,
                "High": price * 1.02,
                "Low": price * 0.98,
                "Close": price,
                "Volume": 10000,
            })

    df_original = pd.DataFrame(records)

    # 1. Compute features & signals on original dataset
    feature_engine = FeatureEngine()
    signal_model = BaselineSignal()

    df_feat1 = feature_engine.transform(df_original)
    df_sig1 = signal_model.generate_signals(df_feat1)

    target_date = pd.Timestamp("2023-06-30")
    signals_at_t_orig = df_sig1[df_sig1["Date"] == target_date].sort_values("Symbol").reset_index(drop=True)

    # 2. Modify future data (data AFTER 2023-06-30, e.g. July & August)
    df_modified = df_original.copy()
    future_mask = df_modified["Date"] > target_date
    df_modified.loc[future_mask, "Close"] = df_modified.loc[future_mask, "Close"] * 10.0  # Massive 10x shock in July/Aug

    # 3. Compute features & signals on modified dataset
    df_feat2 = feature_engine.transform(df_modified)
    df_sig2 = signal_model.generate_signals(df_feat2)

    signals_at_t_mod = df_sig2[df_sig2["Date"] == target_date].sort_values("Symbol").reset_index(drop=True)

    # 4. Assert signals at date T are 100% identical!
    pd.testing.assert_series_equal(
        signals_at_t_orig["FinalScore"],
        signals_at_t_mod["FinalScore"],
        check_names=True,
    )
