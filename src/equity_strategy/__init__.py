"""
Equity Strategy - Quantitative Alpha & Feature Pipeline
"""
from equity_strategy.data.training_dataset import (
    TrainingDatasetBuilder,
    build_training_dataset,
)
from equity_strategy.features.trend import TrendFeatures
from equity_strategy.features.momentum import MomentumFeatures
from equity_strategy.features.volatility import VolatilityFeatures
from equity_strategy.features.engine import FeatureEngine
from equity_strategy.targets.returns import ReturnTargets
from equity_strategy.targets.engine import TargetEngine
from equity_strategy.models.base import BaseModel
from equity_strategy.models.xgboost_regressor import XGBoostRegressor
from equity_strategy.selection.base import BaseSelector
from equity_strategy.selection.percentile import PercentileSelector
from equity_strategy.portfolio.base import BasePortfolio
from equity_strategy.portfolio.equal_weight import EqualWeightPortfolio
from equity_strategy.risk_management.base import BaseRiskManager
from equity_strategy.risk_management.fixed_percentage import FixedPercentageRiskManager

__version__ = "0.1.0"

__all__ = [
    "TrainingDatasetBuilder",
    "build_training_dataset",
    "TrendFeatures",
    "MomentumFeatures",
    "VolatilityFeatures",
    "FeatureEngine",
    "ReturnTargets",
    "TargetEngine",
    "BaseModel",
    "XGBoostRegressor",
    "BaseSelector",
    "PercentileSelector",
    "BasePortfolio",
    "EqualWeightPortfolio",
    "BaseRiskManager",
    "FixedPercentageRiskManager",
    "__version__",
]
