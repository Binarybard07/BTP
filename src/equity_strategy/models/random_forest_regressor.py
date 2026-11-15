from typing import Any, Dict, Optional, Union
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor as SkRandomForestRegressor

from equity_strategy.models.base import BaseModel


class RandomForestRegressor(BaseModel):
    """
    Concrete regression model implementation using a bagged ensemble of decision trees
    (sklearn.ensemble.RandomForestRegressor).

    `max_samples` bounds the bootstrap sample drawn per tree. On ~1M training rows refitted
    at every monthly rebalance, a full-size bootstrap is the dominant runtime cost; a fraction
    such as 0.3 keeps each refit fast with negligible effect on a depth-5 forest.

    Independent of features, targets, universes, ranking, portfolio construction,
    rebalancing, and Nautilus.
    """

    def __init__(
        self,
        n_estimators: int = 100,
        max_depth: Optional[int] = 5,
        min_samples_leaf: int = 1,
        max_features: Union[float, int, str, None] = 1.0,
        max_samples: Optional[float] = 0.3,
        bootstrap: bool = True,
        random_state: int = 42,
        n_jobs: int = -1,
        params: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        model_params: Dict[str, Any] = {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "min_samples_leaf": min_samples_leaf,
            "max_features": max_features,
            "max_samples": max_samples if bootstrap else None,
            "bootstrap": bootstrap,
            "random_state": random_state,
            "n_jobs": n_jobs,
        }
        if params:
            model_params.update(params)
        model_params.update(kwargs)

        self.params: Dict[str, Any] = model_params
        self.model: SkRandomForestRegressor = SkRandomForestRegressor(**self.params)
        self._is_fitted: bool = False

    def train(
        self,
        X: Union[pd.DataFrame, np.ndarray],
        y: Union[pd.Series, np.ndarray],
        **kwargs: Any,
    ) -> "RandomForestRegressor":
        """
        Trains the Random Forest regressor on feature matrix X and target y.
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
