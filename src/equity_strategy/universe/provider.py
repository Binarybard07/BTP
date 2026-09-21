from typing import Dict, List, Optional, Set, Union
import pandas as pd

class UniverseProvider:
    """
    Manages date-dependent stock universe eligibility.

    Supports:
    1. Dynamic historical membership lookup mapping date -> set of eligible symbols.
    2. Default fallback mode: If no dynamic membership schedule is supplied,
       all active symbols in the dataset on that date are treated as eligible.
    """

    def __init__(self, membership_schedule: Optional[Dict[pd.Timestamp, Set[str]]] = None):
        """
        :param membership_schedule: Optional dict mapping Timestamp -> Set[Symbol].
        """
        self.membership_schedule = membership_schedule

    def get_eligible_symbols(
        self,
        date: pd.Timestamp,
        available_symbols: Optional[Union[List[str], Set[str]]] = None
    ) -> Set[str]:
        """
        Returns the set of eligible symbols for a given date.
        """
        ts_date = pd.Timestamp(date)

        if self.membership_schedule is not None:
            # Match exact date or nearest prior date in schedule
            matching_dates = [d for d in self.membership_schedule.keys() if d <= ts_date]
            if matching_dates:
                latest_date = max(matching_dates)
                eligible = set(self.membership_schedule[latest_date])
            else:
                eligible = set()

            if available_symbols is not None:
                eligible = eligible.intersection(set(available_symbols))
            return eligible

        # Fallback mode: all available symbols on that date are eligible
        if available_symbols is not None:
            return set(available_symbols)
        return set()

    def is_eligible(self, symbol: str, date: pd.Timestamp, available_symbols: Optional[Set[str]] = None) -> bool:
        """Checks whether a specific symbol is eligible on a date."""
        eligible = self.get_eligible_symbols(date, available_symbols)
        return symbol in eligible
