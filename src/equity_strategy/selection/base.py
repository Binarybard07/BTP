from abc import ABC, abstractmethod
from typing import List
import pandas as pd


class BaseSelector(ABC):
    """
    Abstract base selector defining the common contract for stock selection.
    """

    @abstractmethod
    def select(self, df: pd.DataFrame, **kwargs) -> List[str]:
        """
        Selects stocks based on prediction scores from a standardized selection DataFrame.

        Parameters:
            df: Standardized DataFrame containing 'Symbol' and 'score' columns.

        Returns:
            List[str]: Selected stock symbols.
        """
        pass

    def __call__(self, df: pd.DataFrame, **kwargs) -> List[str]:
        return self.select(df, **kwargs)
