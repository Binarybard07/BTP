from abc import ABC, abstractmethod
from typing import Any, Union
import numpy as np
import pandas as pd


class BaseModel(ABC):
    """
    Abstract base model defining the common contract for all models.
    """

    @abstractmethod
    def train(
        self,
        X: Union[pd.DataFrame, np.ndarray],
        y: Union[pd.Series, np.ndarray],
        **kwargs: Any,
    ) -> Any:
        """
        Trains the model on feature matrix X and target vector y.
        """
        pass

    @abstractmethod
    def predict(
        self,
        X: Union[pd.DataFrame, np.ndarray],
        **kwargs: Any,
    ) -> np.ndarray:
        """
        Generates predictions for feature matrix X.
        """
        pass
