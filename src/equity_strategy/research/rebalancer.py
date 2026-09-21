from typing import List, Tuple
import pandas as pd

class MonthlyRebalancer:
    """
    Identifies monthly rebalance dates in market data.
    - SignalDate (T): Last available trading session of each calendar month.
    - EffectiveDate (T+1): Next available trading session after SignalDate.
    """

    @staticmethod
    def get_rebalance_dates(dates: pd.Series) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
        """
        Takes a Series/Collection of trading dates and returns list of (SignalDate, EffectiveDate) tuples.
        """
        unique_dates = pd.Series(pd.to_datetime(dates).unique()).sort_values().reset_index(drop=True)
        if len(unique_dates) < 2:
            return []

        # Group by Year and Month to find the last available trading date in each month
        df_dates = pd.DataFrame({"Date": unique_dates})
        df_dates["YearMonth"] = df_dates["Date"].dt.to_period("M")

        # Last trading day of each month
        month_end_indices = df_dates.groupby("YearMonth")["Date"].idxmax().tolist()

        rebalance_pairs = []
        for idx in month_end_indices:
            # Check if there is a next trading day
            if idx + 1 < len(unique_dates):
                signal_date = unique_dates.iloc[idx]
                effective_date = unique_dates.iloc[idx + 1]
                rebalance_pairs.append((signal_date, effective_date))

        return rebalance_pairs
