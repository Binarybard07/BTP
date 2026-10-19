"""
Multi-instrument XGBoost quantitative equity strategy for NautilusTrader.
"""
import copy
import logging
import pathlib
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common import LogLevel
from nautilus_trader.config import FileWriterConfig, LoggerConfig, PortfolioConfig
from nautilus_trader.execution import FeeModel, FillModel, StaticLatencyModel
from nautilus_trader.model import (
    AccountType,
    Bar,
    BarType,
    Currency,
    InstrumentId,
    Money,
    OmsType,
    OrderCanceled,
    OrderCancelRejected,
    OrderFilled,
    PositionChanged,
    PositionClosed,
    PositionOpened,
    TraderId,
    Venue,
)
import pandas as pd

from equity_strategy.data.strategy_data import StrategyDataPipeline
from equity_strategy.data.training_dataset import (
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    TrainingDatasetBuilder,
)
from equity_strategy.execution import (
    IndianEquityDeliveryFeeModel,
    PercentSlippage,
    SlippageFillModel,
)
from equity_strategy.logging_config import configure_logging
from equity_strategy.models.base import BaseModel
from equity_strategy.models.xgboost_regressor import XGBoostRegressor
from equity_strategy.portfolio.base import BasePortfolio
from equity_strategy.portfolio.equal_weight import EqualWeightPortfolio
from equity_strategy.position_manager import PositionManager
from equity_strategy.rebalancing.rebalancer import Rebalancer
from equity_strategy.reporting import NautilusExporter
from equity_strategy.risk_management import BaseRiskManager, FixedPercentageRiskManager
from equity_strategy.selection.base import BaseSelector
from equity_strategy.selection.percentile import PercentileSelector
from equity_strategy.selection.top_n import TopNSelector
from equity_strategy.strategies.base import BaseStrategy, BaseStrategyConfig
from equity_strategy.targets.engine import TargetEngine
from equity_strategy.trade_manager import (
    ATRTradeManager,
    BaseTradeManager,
    FixedTPSLTradeManager,
)

logger = logging.getLogger(__name__)


class XGBoostRegressorConfig(BaseStrategyConfig):
    """
    Configuration for XGBoostRegressorStrategy.

    All core strategy execution dependencies (model, selector, portfolio, risk_manager,
    fee_model, fill_model) must be explicitly configured and supplied. Missing required
    dependencies will raise an immediate error.
    """

    def __init__(
        self,
        model: BaseModel,
        selector: BaseSelector,
        portfolio: BasePortfolio,
        risk_manager: BaseRiskManager,
        fee_model: FeeModel,
        fill_model: FillModel,
        target_column: str,
        feature_columns: List[str],
        venue: str = "NSE",
        currency: str = "INR",
        exchange_tz: str = "Asia/Kolkata",
        min_train_days: int = 252,
        target_horizon: int = 10,
        position_manager: Optional[PositionManager] = None,
    ) -> None:
        super().__init__(
            venue=venue,
            currency=currency,
            exchange_tz=exchange_tz,
        )
        self.model: BaseModel = model
        self.selector: BaseSelector = selector
        self.portfolio: BasePortfolio = portfolio
        self.risk_manager: BaseRiskManager = risk_manager
        self.fee_model: FeeModel = fee_model
        self.fill_model: FillModel = fill_model
        self.position_manager: Optional[PositionManager] = position_manager

        self.min_train_days: int = int(min_train_days)
        self.target_horizon: int = int(target_horizon)
        self.target_column: str = target_column
        self.feature_columns: List[str] = list(feature_columns)


class XGBoostRegressorStrategy(BaseStrategy):
    """
    Multi-Instrument XGBoost ML Strategy orchestrator on NautilusTrader.

    Lifecycle:
        Daily Bars (15:30 IST)
          ↓
        Month-End Session T Detection on on_bar()
          ↓
        Expanding-Window XGBoost Model Training (cutoff: T - target_horizon)
          ↓
        Cross-Sectional Prediction & Stock Selection
          ↓
        Equal-Weight Target Allocation
          ↓
        Direct Rebalance Order Submission (Sells first, then Buys)
          ↓
        Orders fill against Opening Quotes at T+1 Open (09:15 IST)
    """

    def __new__(
        cls,
        config: XGBoostRegressorConfig,
        *args: Any,
        **kwargs: Any,
    ) -> "XGBoostRegressorStrategy":
        return super().__new__(cls, config=config)

    def __init__(
        self,
        config: XGBoostRegressorConfig,
        bar_types: List[BarType],
        featured_df: pd.DataFrame,
        training_df: pd.DataFrame,
        all_trading_dates: List[pd.Timestamp],
        instruments_by_date: Dict[pd.Timestamp, Set[InstrumentId]],
    ) -> None:
        super().__init__(config=config, bar_types=bar_types)
        self.featured_df = featured_df
        self.training_df = training_df
        self.all_trading_dates = all_trading_dates
        self.instruments_by_date = instruments_by_date
        self.date_to_index: Dict[pd.Timestamp, int] = {
            d: i for i, d in enumerate(self.all_trading_dates)
        }
        self._symbol_to_instrument_id: Dict[str, InstrumentId] = {
            str(bt.instrument_id.symbol).upper(): bt.instrument_id for bt in self.bar_types
        }
        self._latest_prices: Dict[InstrumentId, float] = {}

        self.feature_columns: List[str] = config.feature_columns
        self.target_horizon: int = config.target_horizon
        self.target_column: str = config.target_column
        self.min_train_days: int = config.min_train_days

        self.model_prototype: BaseModel = config.model
        self.selector: BaseSelector = config.selector
        self.portfolio_constructor: BasePortfolio = config.portfolio
        self.risk_manager: BaseRiskManager = config.risk_manager
        self.fee_model: FeeModel = config.fee_model
        self.fill_model: FillModel = config.fill_model
        self.position_manager: Optional[PositionManager] = config.position_manager
        self.rebalancer: Optional[Rebalancer] = None

        # Multi-instrument synchronization and decision tracking
        self._session_reported_bars: Dict[pd.Timestamp, Set[InstrumentId]] = {}
        self._rebalance_plans_by_decision_date: Set[pd.Timestamp] = set()

    @property
    def trade_manager(self) -> Optional[BaseTradeManager]:
        """Provides backward-compatible access to the configured TradeManager."""
        return self.position_manager.trade_manager if self.position_manager is not None else None

    def on_start(self) -> None:
        """Initializes subscriptions and execution infrastructure."""
        super().on_start()

        # Subscribe to opening quotes so submitted market orders match at next session open
        for bar_type in self.bar_types:
            self.subscribe_quotes(bar_type.instrument_id)

        # Wire position manager with strategy runtime
        if self.position_manager is not None:
            self.position_manager.initialize(strategy=self)

        # Wire rebalancer with authoritative strategy risk manager
        self.rebalancer = Rebalancer(
            strategy=self,
            default_venue=self.venue,
            base_currency=self.currency,
            risk_manager=self.risk_manager,
        )

        # Attach simulation clock to fee model if supported
        if self.fee_model is not None and hasattr(self.fee_model, "set_clock"):
            self.fee_model.set_clock(self.clock)

        logger.info(
            "XGBoostRegressorStrategy started with %d instruments.", len(self.bar_types)
        )

    def on_position_opened(self, event: PositionOpened) -> None:
        """Position opened callback: delegates protection setup to PositionManager."""
        if self.position_manager is not None:
            self.position_manager.on_position_opened(event)

    def on_position_changed(self, event: PositionChanged) -> None:
        """Position changed callback: delegates protection reconciliation to PositionManager."""
        if self.position_manager is not None:
            self.position_manager.on_position_changed(event)

    def on_position_closed(self, event: PositionClosed) -> None:
        """Position closed callback: delegates protection cancellation to PositionManager."""
        if self.position_manager is not None:
            self.position_manager.on_position_closed(event)

    def on_order_canceled(self, event: OrderCanceled) -> None:
        """Order canceled callback: handles asynchronous protection replacement."""
        if self.position_manager is not None:
            self.position_manager.on_order_canceled(event)

    def on_order_cancel_rejected(self, event: OrderCancelRejected) -> None:
        """Order cancel rejected callback: notifies PositionManager."""
        if self.position_manager is not None:
            self.position_manager.on_order_cancel_rejected(event)

    def on_order_filled(self, event: OrderFilled) -> None:
        """Delegates fill reconciliation to slippage fill model and PositionManager."""
        if self.fill_model is not None and hasattr(self.fill_model, "record_fill"):
            self.fill_model.record_fill(event)
        if self.position_manager is not None:
            self.position_manager.on_order_filled(event)

    def on_bar(self, bar: Bar) -> None:
        """
        Sole rebalance trigger:
        1. Tracks latest close prices from Nautilus execution bars.
        2. Synchronizes across universe for session T.
        3. Once all expected instruments report, evaluates month-end decision.
        4. Submits rebalance orders at T close.
        """
        inst_id = bar.bar_type.instrument_id
        self._latest_prices[inst_id] = float(bar.close.as_double())
        bar_date = self.extract_session_date(bar)

        if bar_date not in self._session_reported_bars:
            self._session_reported_bars[bar_date] = set()
        self._session_reported_bars[bar_date].add(inst_id)

        expected_instruments = self.instruments_by_date.get(bar_date)
        if expected_instruments and self._session_reported_bars[bar_date] >= expected_instruments:
            if bar_date not in self.date_to_index:
                raise KeyError(f"Bar date {bar_date} not found in trading calendar.")
            date_idx = self.date_to_index[bar_date]

            is_month_end = (
                date_idx == len(self.all_trading_dates) - 1
                or self.all_trading_dates[date_idx + 1].month != bar_date.month
            )
            if is_month_end and bar_date not in self._rebalance_plans_by_decision_date:
                logger.info(
                    "Month-end trading session T close reached: %s. Executing rebalance...",
                    bar_date.strftime("%Y-%m-%d"),
                )
                self._execute_rebalance(bar_date)

    def _create_fresh_model(self) -> BaseModel:
        """Creates an unfitted model instance preserving configured hyperparameters."""
        return copy.deepcopy(self.model_prototype)

    def _execute_rebalance(self, decision_date: pd.Timestamp) -> None:
        """
        Executes lookahead-free training, prediction, portfolio construction,
        and rebalance order submission at session T close.
        """
        self._rebalance_plans_by_decision_date.add(decision_date)
        date_idx = self.date_to_index[decision_date]

        # Minimum training history requirement
        if date_idx < self.min_train_days:
            logger.info(
                "Skipping rebalance at %s: elapsed trading days (%d) < min_train_days (%d)",
                decision_date.strftime("%Y-%m-%d"),
                date_idx,
                self.min_train_days,
            )
            return

        # Lookahead prevention: cutoff at T - target_horizon
        cutoff_idx = max(0, date_idx - self.target_horizon)
        train_cutoff_date = self.all_trading_dates[cutoff_idx]

        train_subset = self.training_df[self.training_df["Date"] <= train_cutoff_date]
        if train_subset.empty:
            logger.warning(
                "Training dataset is empty for cutoff date %s. Skipping rebalance.",
                train_cutoff_date.strftime("%Y-%m-%d"),
            )
            return

        X_train = train_subset[self.feature_columns]
        y_train = train_subset[self.target_column]

        logger.info(
            "Training pooled model for rebalance %s (cutoff: %s, train rows: %d)...",
            decision_date.strftime("%Y-%m-%d"),
            train_cutoff_date.strftime("%Y-%m-%d"),
            len(train_subset),
        )
        model = self._create_fresh_model()
        model.train(X_train, y_train)

        # Cross-sectional features on decision date
        current_bar_data = self.featured_df[self.featured_df["Date"] == decision_date].copy()
        valid_current = current_bar_data.dropna(subset=self.feature_columns)
        if valid_current.empty:
            logger.warning(
                "No valid feature rows on decision date %s. Skipping rebalance.",
                decision_date.strftime("%Y-%m-%d"),
            )
            return

        X_pred = valid_current[self.feature_columns]
        scores = model.predict(X_pred)

        selection_df = pd.DataFrame({
            "Symbol": valid_current["Symbol"].astype(str).values,
            "score": scores,
        })

        # Stock selection and portfolio construction
        selected_symbols = self.selector.select(selection_df)
        if not selected_symbols:
            logger.warning("Selector returned empty list for date %s.", decision_date)
            return

        target_weights_by_symbol = self.portfolio_constructor.construct(selected_symbols)
        if not target_weights_by_symbol:
            logger.warning(
                "Portfolio constructor returned empty weights for date %s.", decision_date
            )
            return

        # Map target weights to InstrumentId
        target_weights: Dict[Union[InstrumentId, str], float] = {}
        for sym, w in target_weights_by_symbol.items():
            sym_clean = sym.strip().upper()
            inst_id = self._symbol_to_instrument_id.get(sym_clean)
            if inst_id is not None:
                target_weights[inst_id] = w
            else:
                target_weights[f"{sym_clean}.{self.venue}"] = w

        # Direct rebalance execution using authoritative latest closing prices
        logger.info(
            "Invoking Rebalancer for %d target symbols (Total Weight: %.4f) on %s...",
            len(target_weights),
            sum(target_weights.values()),
            decision_date.strftime("%Y-%m-%d"),
        )
        orders = self.rebalancer.rebalance(
            target_weights=target_weights,
            current_prices=self._latest_prices,
        )
        logger.info(
            "Rebalance for %s complete: %d orders submitted directly.",
            decision_date.strftime("%Y-%m-%d"),
            len(orders),
        )

    @classmethod
    def run(
        cls,
        config: XGBoostRegressorConfig,
        data_source: Union[str, pathlib.Path, pd.DataFrame] = "data/raw/NIFTY50",
        symbol: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        starting_balance: float = 10_000_000.0,
        pipeline: Optional[StrategyDataPipeline] = None,
        engine_config: Optional[BacktestEngineConfig] = None,
        unique_id: Optional[str] = None,
    ) -> BacktestEngine:
        """
        Executes a multi-instrument NautilusTrader backtest for XGBoostRegressorStrategy.
        """
        strat_config = config
        venue = strat_config.venue
        currency = strat_config.currency
        unique_id = "001" if unique_id is None else unique_id
        target_horizon = strat_config.target_horizon
        target_column = strat_config.target_column
        f_cols = strat_config.feature_columns

        items_to_process: List[Tuple[Union[str, pathlib.Path, pd.DataFrame], Optional[str]]] = []
        if isinstance(data_source, pd.DataFrame):
            items_to_process.append((data_source, symbol))
        else:
            p = pathlib.Path(data_source)
            if p.is_file():
                sym = p.stem.strip().upper() if symbol is None else symbol
                items_to_process.append((p, sym))
            elif p.is_dir():
                if symbol:
                    target_csv = p / f"{symbol.strip().upper()}.csv"
                    if target_csv.exists():
                        items_to_process.append((target_csv, symbol.strip().upper()))
                    else:
                        raise FileNotFoundError(f"Symbol file '{target_csv}' not found in directory.")
                else:
                    csv_files = sorted(p.glob("*.csv"))
                    if not csv_files:
                        raise FileNotFoundError(f"No CSV files found in directory: {p}")
                    for f in csv_files:
                        items_to_process.append((f, f.stem.strip().upper()))
            else:
                raise FileNotFoundError(f"Data source not found: {data_source}")

        pipe = pipeline if pipeline is not None else StrategyDataPipeline()

        run_dir = pathlib.Path("strategy_results") / unique_id
        run_dir.mkdir(parents=True, exist_ok=True)

        if engine_config is None:
            cfg = BacktestEngineConfig(
                trader_id=TraderId.from_str("TESTER-001"),
                portfolio=PortfolioConfig(equity_curve=False),
                logging=LoggerConfig(
                    stdout_level=LogLevel.OFF,
                    fileout_level=LogLevel.INFO,
                    component_levels={"Portfolio": "INFO"},
                    file_config=FileWriterConfig(
                        directory=str(run_dir),
                        file_name="nautilus.log",
                    ),
                ),
            )
        else:
            cfg = engine_config
        engine = BacktestEngine(config=cfg)

        engine.add_venue(
            venue=Venue(venue),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(starting_balance, Currency.from_str(currency))],
            fee_model=strat_config.fee_model,
            fill_model=strat_config.fill_model,
            latency_model=StaticLatencyModel(insert_latency_nanos=1_000_000),
        )

        bar_types: List[BarType] = []
        featured_dfs: List[pd.DataFrame] = []
        skipped_files: List[Tuple[str, Optional[str], str]] = []

        for src, sym in items_to_process:
            filename = (
                pathlib.Path(src).name
                if not isinstance(src, pd.DataFrame)
                else "<DataFrame>"
            )
            try:
                prepared = pipe.prepare(
                    data_source=src,
                    symbol=sym,
                    venue=venue,
                    currency=currency,
                    validate=True,
                )
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "[DATA VALIDATION SKIP] File: '%s', Symbol: '%s' -> Error: %s",
                    filename,
                    sym,
                    err_msg,
                )
                skipped_files.append((filename, sym, err_msg))
                continue

            engine.add_instrument(prepared.equity)
            engine.add_data(prepared.bars)
            if prepared.opening_quotes:
                engine.add_data(prepared.opening_quotes)
            bar_types.append(prepared.bar_type)
            featured_dfs.append(prepared.featured_df)

        if not bar_types:
            raise RuntimeError(
                f"No valid instruments were loaded for backtest. All {len(items_to_process)} sources failed validation."
            )

        if skipped_files:
            logger.info(
                "Data validation summary: %d loaded, %d skipped.",
                len(bar_types),
                len(skipped_files),
            )
            for fname, s, err in skipped_files:
                logger.warning("  - File: '%s', Symbol: '%s' -> %s", fname, s, err)

        combined_featured_df = pd.concat(featured_dfs, ignore_index=True)
        combined_featured_df["Date"] = pd.to_datetime(combined_featured_df["Date"])
        combined_featured_df = combined_featured_df.sort_values(
            by=["Symbol", "Date"]
        ).reset_index(drop=True)

        missing_features = [
            col for col in f_cols if col not in combined_featured_df.columns
        ]
        if missing_features:
            raise ValueError(
                f"Configured feature_columns missing from featured data: {missing_features}"
            )

        target_eng = TargetEngine(horizon=target_horizon, target_col=target_column)
        targeted_df = target_eng.transform(combined_featured_df)
        targeted_df["Date"] = pd.to_datetime(targeted_df["Date"])

        builder = TrainingDatasetBuilder(
            feature_columns=f_cols,
            target_column=target_column,
        )
        training_df = builder.build(targeted_df)
        training_df["Date"] = pd.to_datetime(training_df["Date"])

        all_trading_dates = (
            pd.Series(combined_featured_df["Date"].unique())
            .sort_values()
            .reset_index(drop=True)
            .tolist()
        )

        instruments_by_date: Dict[pd.Timestamp, Set[InstrumentId]] = {}
        for dt, grp in combined_featured_df.groupby("Date"):
            instruments_by_date[pd.Timestamp(dt)] = {
                InstrumentId.from_str(f"{s.strip().upper()}.{venue}")
                for s in grp["Symbol"]
            }

        engine.sort_data()

        strategy = cls(
            config=strat_config,
            bar_types=bar_types,
            featured_df=combined_featured_df,
            training_df=training_df,
            all_trading_dates=all_trading_dates,
            instruments_by_date=instruments_by_date,
        )
        engine.add_strategy(strategy)

        start_ts = None
        end_ts = None
        if start_date is not None:
            start_ts = (
                pd.Timestamp(start_date)
                .tz_localize("Asia/Kolkata")
                .tz_convert("UTC")
                .value
            )
        if end_date is not None:
            end_ts = (
                pd.Timestamp(end_date)
                .normalize()
                .tz_localize("Asia/Kolkata")
                + pd.Timedelta(days=1)
                - pd.Timedelta(nanoseconds=1)
            ).tz_convert("UTC").value

        if start_ts is not None and end_ts is not None and start_ts >= end_ts:
            raise ValueError("start_date must be before end_date")

        if start_ts is not None and end_ts is not None:
            engine.run(start=start_ts, end=end_ts)
        elif start_ts is not None:
            engine.run(start=start_ts)
        elif end_ts is not None:
            engine.run(end=end_ts)
        else:
            engine.run()

        NautilusExporter.export(
            engine=engine,
            run_dir=run_dir,
            venue=venue,
            currency=currency,
        )
        return engine


if __name__ == "__main__":

    configure_logging()
    data_source = "NIFTY50"
    logger.info("Starting XGBoost Regressor Strategy Backtest...")
    unique_id = data_source + "_" + pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")

    # Configure components explicitly at the application entry point
    model = XGBoostRegressor()
    selector = PercentileSelector(percentile=85.0)
    portfolio = EqualWeightPortfolio()
    risk_manager = FixedPercentageRiskManager(max_deployment_pct=0.75)
    trade_manager = FixedTPSLTradeManager(take_profit_pct=0.10, stop_loss_pct=0.05)
    position_manager = PositionManager(trade_manager=trade_manager)
    fee_model = IndianEquityDeliveryFeeModel()
    fill_model = SlippageFillModel(method=PercentSlippage(0.0005))
    features = FEATURE_COLUMNS
    targets = TARGET_COLUMN

    config = XGBoostRegressorConfig(
        min_train_days=252,
        model=model,
        selector=selector,
        portfolio=portfolio,
        risk_manager=risk_manager,
        position_manager=position_manager,
        fee_model=fee_model,
        fill_model=fill_model,
        feature_columns=features,
        target_column=targets
    )

    engine = XGBoostRegressorStrategy.run(
        config=config,
        data_source=f"data/raw/{data_source}",
        start_date="2024-01-01",
        starting_balance=10_000_000.0,
        unique_id=unique_id,
    )

    logger.info("=== Multi-Instrument XGBoost Regressor Backtest Completed: %s ===", unique_id)