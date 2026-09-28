from typing import Dict, List

from equity_strategy.portfolio.base import BasePortfolio


class EqualWeightPortfolio(BasePortfolio):
    """
    Constructs an equal-weighted target portfolio from selected stock symbols.

    Formula:
        weight = 1.0 / len(selected_stocks)
    """

    def construct(self, symbols: List[str]) -> Dict[str, float]:
        """
        Calculates equal target weights for all provided symbols.

        Parameters:
            symbols: List of selected stock symbols.

        Returns:
            Dict[str, float]: Mapping of symbol to equal weight (1 / N).
                              Returns empty dict if no symbols are provided.
        """
        if not symbols:
            return {}

        # Preserve order while deduplicating any accidental duplicate symbols
        unique_symbols = list(dict.fromkeys(str(s) for s in symbols if s))
        n = len(unique_symbols)
        if n == 0:
            return {}

        weight = 1.0 / n
        return {sym: weight for sym in unique_symbols}
