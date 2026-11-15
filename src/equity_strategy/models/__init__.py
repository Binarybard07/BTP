from equity_strategy.models.base import BaseModel
from equity_strategy.models.xgboost_regressor import XGBoostRegressor
from equity_strategy.models.ridge_regressor import RidgeRegressor
from equity_strategy.models.lasso_regressor import LassoRegressor
from equity_strategy.models.random_forest_regressor import RandomForestRegressor

__all__ = [
    "BaseModel",
    "XGBoostRegressor",
    "RidgeRegressor",
    "LassoRegressor",
    "RandomForestRegressor",
]

# LightGBM is an optional dependency: import it only if installed so the rest of the
# package keeps working on environments without it (`uv add lightgbm` to enable).
try:
    from equity_strategy.models.lightgbm_regressor import LightGBMRegressor  # noqa: F401
    __all__.append("LightGBMRegressor")
except ImportError:  # pragma: no cover
    pass
