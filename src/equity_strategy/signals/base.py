from abc import ABC, abstractmethod
import pandas as pd

class SignalModel(ABC):
    """
    Abstract interface for trading signal models.
    Future models (e.g. XGBoostRanker, Ensembles) will inherit from this base class.
    """

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculates signal scores for the input dataframe.
        Must return DataFrame containing Symbol, Date, and FinalScore.
        """
        pass
