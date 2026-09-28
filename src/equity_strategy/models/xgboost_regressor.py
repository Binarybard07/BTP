from typing import Any, Dict, Optional, Union
import numpy as np
import pandas as pd
import xgboost as xgb

from equity_strategy.models.base import BaseModel


class XGBoostRegressor(BaseModel):
    """
    Concrete regression model implementation using XGBoost regression (xgb.XGBRegressor).

    Independent of features, targets, universes, ranking, portfolio construction,
    rebalancing, and Nautilus.
    """

    def __init__(
        self,
        n_estimators: int = 100,
        max_depth: int = 3,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        random_state: int = 42,
        n_jobs: int = -1,
        params: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ):
        model_params: Dict[str, Any] = {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "learning_rate": learning_rate,
            "subsample": subsample,
            "colsample_bytree": colsample_bytree,
            "random_state": random_state,
            "n_jobs": n_jobs,
        }
        if params:
            model_params.update(params)
        model_params.update(kwargs)

        self.params: Dict[str, Any] = model_params
        self.model: xgb.XGBRegressor = xgb.XGBRegressor(**self.params)
        self._is_fitted: bool = False

    def train(
        self,
        X: Union[pd.DataFrame, np.ndarray],
        y: Union[pd.Series, np.ndarray],
        **kwargs: Any,
    ) -> "XGBoostRegressor":
        """
        Trains the XGBoost regressor on feature matrix X and target y.
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
