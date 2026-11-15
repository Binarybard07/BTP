from typing import Any, Dict, Optional, Union
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from equity_strategy.models.base import BaseModel


class RidgeRegressor(BaseModel):
    """
    Concrete regression model implementation using L2-regularised linear regression
    (sklearn.linear_model.Ridge).

    Features are standardised (zero mean, unit variance) inside the pipeline so the
    L2 penalty acts equally on every feature regardless of its native scale. The scaler
    is fitted on the training window only, so no future information leaks into inference.

    Independent of features, targets, universes, ranking, portfolio construction,
    rebalancing, and Nautilus.
    """

    def __init__(
        self,
        alpha: float = 100.0,
        fit_intercept: bool = True,
        standardize: bool = True,
        random_state: int = 42,
        params: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        model_params: Dict[str, Any] = {
            "alpha": alpha,
            "fit_intercept": fit_intercept,
            "random_state": random_state,
        }
        if params:
            model_params.update(params)
        model_params.update(kwargs)

        self.params: Dict[str, Any] = model_params
        self.standardize: bool = bool(standardize)

        steps = []
        if self.standardize:
            steps.append(("scaler", StandardScaler()))
        steps.append(("model", Ridge(**self.params)))
        self.model: Pipeline = Pipeline(steps)
        self._is_fitted: bool = False

    def train(
        self,
        X: Union[pd.DataFrame, np.ndarray],
        y: Union[pd.Series, np.ndarray],
        **kwargs: Any,
    ) -> "RidgeRegressor":
        """
        Trains the Ridge regressor on feature matrix X and target y.
        """
        self.model.fit(X, y, **kwargs)
        self._is_fitted = True
        return self

    def predict(
        self,
        X: Union[pd.DataFrame, np.ndarray],
        **kwargs: Any,
    ) -> np.ndarray:
        """
        Generates continuous predictions for feature matrix X.
        """
        if not self._is_fitted:
            raise RuntimeError("Model must be trained before calling predict().")
        preds = self.model.predict(X, **kwargs)
        return np.asarray(preds)

    @property
    def is_fitted(self) -> bool:
        """Returns True if the model has been trained."""
        return self._is_fitted
