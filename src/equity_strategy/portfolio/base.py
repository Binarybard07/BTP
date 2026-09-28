from abc import ABC, abstractmethod
from typing import Dict, List


class BasePortfolio(ABC):
    """
    Abstract base class defining the minimal contract for portfolio construction.
    """

    @abstractmethod
    def construct(self, symbols: List[str]) -> Dict[str, float]:
        """
        Constructs portfolio target weights from a list of selected stock symbols.

        Parameters:
            symbols: List of selected stock symbols.

        Returns:
            Dict[str, float]: Mapping from each symbol to its target portfolio weight.
        """
        pass

    def __call__(self, symbols: List[str]) -> Dict[str, float]:
        """Callable interface delegating to construct()."""
        return self.construct(symbols)
