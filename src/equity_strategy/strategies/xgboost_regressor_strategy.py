import copy
import logging
import pathlib
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from nautilus_trader.common import LogLevel
from nautilus_trader.config import FileWriterConfig
from nautilus_trader.config import LoggerConfig
from nautilus_trader.model import TraderId
import pandas as pd
from equity_strategy.logging_config import configure_logging

from nautilus_trader.analysis import create_tearsheet
from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model import (
    AccountType,
    Bar,
    BarType,
    ClientOrderId,
    Currency,
    Equity,
    InstrumentId,
    Money,
    OmsType,
    OrderFilled,
    OrderSide,
    Price,
    Quantity,
    QuoteTick,
    Venue,
)
from nautilus_trader.execution import FeeModel, FillModel, StaticLatencyModel
from nautilus_trader.trading import Strategy, StrategyConfig

from equity_strategy.strategies.base import BaseExecutionConfig, BaseExecutionStrategy
from equity_strategy.execution import (
    AdverseSlippageConfig,
    AdverseSlippageFillModel,
    DPChargesConfig,
    ExecutionCostReport,
    ExecutionEventRecorder,
    IndianEquityDeliveryFeeModel,
    OrdinaryChargesConfig,
)
from equity_strategy.data.loader import CSVDataLoader
from equity_strategy.data.nautilus_data import NautilusDataConverter
from equity_strategy.data.strategy_data import StrategyDataPipeline
from equity_strategy.data.training_dataset import (
    TrainingDatasetBuilder,
    FEATURE_COLUMNS,
    TARGET_COLUMN,
)
from equity_strategy.features.engine import FeatureEngine
from equity_strategy.models.base import BaseModel
from equity_strategy.models.xgboost_regressor import XGBoostRegressor
from equity_strategy.portfolio.base import BasePortfolio
from equity_strategy.portfolio.equal_weight import EqualWeightPortfolio
from equity_strategy.rebalancing.base import BaseRebalanceStrategy
from equity_strategy.rebalancing.monthly import MonthlyRebalanceStrategy
from equity_strategy.rebalancing.rebalancer import (
    PlannedOrderInstruction,
    RebalancePlan,
    Rebalancer,
)
from equity_strategy.reporting import (
    CostExporter,
    MarketDataExporter,
    NautilusExporter,
    RunStore,
    StrategyExporter,
)
from equity_strategy.selection.base import BaseSelector
from equity_strategy.selection.top_n import TopNSelector
from equity_strategy.selection.percentile import PercentileSelector
from equity_strategy.targets import engine
from equity_strategy.targets.engine import TargetEngine

logger = logging.getLogger(__name__)


class XGBoostRegressorConfig(BaseExecutionConfig):
    """
    Configuration for the event-driven XGBoostRegressorStrategy.

    Parameters:
        bar_types: List of Nautilus BarType subscriptions for the active universe.
        featured_df: Precomputed historical feature DataFrame for all valid instruments.
        training_df: Clean training dataset with features and realized target_10d returns.
        all_trading_dates: Chronologically sorted unique trading dates across the universe.
        instruments_by_date: Mapping of session date to active instruments in that session.
        feature_columns: Model feature column names (default: 6 technical features).
        target_horizon: Forward return horizon in trading days (default: 10).
        target_column: Target column name (default: 'target_10d').
        min_train_days: Minimum historical trading days before first rebalance (default: 252).
        top_n: Number of top-ranked stocks to select (default: 20).
        model: Swappable ML regressor prototype adhering to BaseModel.
        selector: Swappable stock selector adhering to BaseSelector.
        portfolio: Swappable portfolio constructor adhering to BasePortfolio.
        rebalance_strategy: Swappable rebalance scheduler adhering to BaseRebalanceStrategy.
        rebalancer: Optional execution Rebalancer instance.
        venue: Exchange venue code (default: 'NSE').
        currency: Base account currency (default: 'INR').
    """

    def __init__(
        self,
        bar_types: List[BarType],
        featured_df: Optional[pd.DataFrame] = None,
        training_df: Optional[pd.DataFrame] = None,
        all_trading_dates: Optional[List[pd.Timestamp]] = None,
        instruments_by_date: Optional[Dict[pd.Timestamp, Set[InstrumentId]]] = None,
        feature_columns: Optional[List[str]] = None,
        target_horizon: int = 10,
        target_column: str = TARGET_COLUMN,
        min_train_days: int = 252,
        top_n: int = 20,
        model: Optional[BaseModel] = None,
        selector: Optional[BaseSelector] = None,
        portfolio: Optional[BasePortfolio] = None,
        rebalance_strategy: Optional[BaseRebalanceStrategy] = None,
        rebalancer: Optional[Rebalancer] = None,
        venue: str = "NSE",
        currency: str = "INR",
        fee_model: Optional[FeeModel] = None,
        fill_model: Optional[FillModel] = None,
        recorder: Optional[ExecutionEventRecorder] = None,
        ordinary_charges_config: Optional[OrdinaryChargesConfig] = None,
        dp_charges_config: Optional[DPChargesConfig] = None,
        slippage_pct: float = 0.0005,
        enable_slippage: bool = True,
        exchange_tz: str = "Asia/Kolkata",
        **kwargs: Any,
    ):
        super().__init__(
            bar_types=bar_types,
            all_trading_dates=all_trading_dates,
            venue=venue,
            currency=currency,
            rebalancer=rebalancer,
            fee_model=fee_model,
            fill_model=fill_model,
            recorder=recorder,
            exchange_tz=exchange_tz,
            **kwargs,
        )
        self.featured_df = featured_df
        self.training_df = training_df
        self.instruments_by_date = instruments_by_date or {}
        self.feature_columns = list(feature_columns or FEATURE_COLUMNS)
        self.target_horizon = target_horizon
        self.target_column = target_column
        self.min_train_days = min_train_days
        self.top_n = top_n
        self.percentile = 85.0
        self.model = model or XGBoostRegressor()
        self.selector = selector or PercentileSelector(percentile=self.percentile)
        self.portfolio = portfolio or EqualWeightPortfolio()
        self.rebalance_strategy = rebalance_strategy or MonthlyRebalanceStrategy()
        self.ordinary_charges_config = ordinary_charges_config
        self.dp_charges_config = dp_charges_config
        self.slippage_pct = slippage_pct
        self.enable_slippage = enable_slippage


class XGBoostRegressorStrategy(BaseExecutionStrategy):
    """
    Concrete Multi-Instrument XGBoost ML Strategy running on NautilusTrader.

    Orchestrates:
        Nautilus Bars (daily)
          ↓
        MonthlyRebalanceStrategy (causal calendar-month transition detection)
          ↓
        Expanding-Window XGBoost Model Training (strictly <= decision_date - 10 trading days)
          ↓
        Return Predictions on decision_date features
          ↓
        Top-N Stock Selection (Top 20)
          ↓
        Equal-Weight Target Portfolio Construction (1/N)
          ↓
        Rebalancer (equity-based sizing, lot size constraints, sells before buys)
          ↓
        Nautilus Market Orders via BaseExecutionStrategy T+1 Open Execution
    """

    def __init__(self, config: XGBoostRegressorConfig):
        super().__init__(config=config)
        self.featured_df: pd.DataFrame = (
            config.featured_df if config.featured_df is not None else pd.DataFrame()
        )
        self.training_df: pd.DataFrame = (
            config.training_df if config.training_df is not None else pd.DataFrame()
        )
        self.instruments_by_date: Dict[pd.Timestamp, Set[InstrumentId]] = (
            config.instruments_by_date
        )
        self.feature_columns: List[str] = config.feature_columns
        self.target_horizon: int = config.target_horizon
        self.target_column: str = config.target_column
        self.min_train_days: int = config.min_train_days
        self.top_n: int = config.top_n
        self.model_prototype: BaseModel = config.model
        self.selector: BaseSelector = config.selector
        self.portfolio_constructor: BasePortfolio = config.portfolio
        self.rebalance_strategy: BaseRebalanceStrategy = config.rebalance_strategy

        # Session tracking for multi-instrument bar collection
        self._session_reported_bars: Dict[pd.Timestamp, Set[InstrumentId]] = {}

        # Rebalance planning session records
        self._rebalance_plans_by_decision_date: Set[pd.Timestamp] = set()

        # Strategy telemetry and research history
        self.rebalance_dates: List[pd.Timestamp] = []
        self.weights_by_date: Dict[pd.Timestamp, Dict[str, float]] = {}
        self.selected_stocks_by_date: Dict[pd.Timestamp, List[str]] = {}
        self.predictions_by_date: Dict[pd.Timestamp, pd.DataFrame] = {}
        self.orders_by_date: Dict[pd.Timestamp, List[Any]] = {}

    def on_start(self) -> None:
        """
        Nautilus lifecycle callback:
        Initializes base execution infrastructure, subscribes to bars and opening quotes,
        wires Rebalancer, and resets the MonthlyRebalanceStrategy.
        """
        super().on_start()
        self.rebalance_strategy.reset()
        logger.info(
            "XGBoostRegressorStrategy started with %d instruments.", len(self.bar_types)
        )

    def on_bar(self, bar: Bar) -> None:
        """
        Nautilus lifecycle callback for daily closing bars (15:30 IST):
        1. Tracks latest close price for the incoming instrument (via base class).
        2. Tracks bars reporting in the current session.
        3. Once all expected instruments have reported their session T bars, checks
           whether session T is the final trading session of the calendar month (T close).
        4. If session T is month-end, plans the rebalance using strictly data available
           through session T close, and queues orders for execution at session T+1 open.
           NO orders are submitted on session T.
        """
        super().on_bar(bar)
        inst_id = bar.bar_type.instrument_id
        bar_date = self.extract_session_date(bar)

        if bar_date not in self._session_reported_bars:
            self._session_reported_bars[bar_date] = set()
        self._session_reported_bars[bar_date].add(inst_id)

        expected_instruments = self.instruments_by_date.get(bar_date)
        if expected_instruments and self._session_reported_bars[bar_date] >= expected_instruments:
            # Check if bar_date is month-end trading session T
            date_idx = self.date_to_index.get(bar_date)
            if date_idx is not None:
                is_month_end = (
                    date_idx == len(self.all_trading_dates) - 1
                    or self.all_trading_dates[date_idx + 1].month != bar_date.month
                )
                if is_month_end and bar_date not in self._rebalance_plans_by_decision_date:
                    logger.info(
                        "Month-end trading session T close reached: %s. Planning lookahead-free rebalance...",
                        bar_date.strftime("%Y-%m-%d"),
                    )
                    self._plan_and_queue_rebalance(bar_date)

    def on_quote(self, quote: QuoteTick) -> None:
        """
        Nautilus lifecycle callback for opening QuoteTicks (09:15 IST):
        1. Evaluates causal monthly scheduler fallback if not already planned at T close.
        2. Delegates T+1 opening execution release to base strategy coordinator.
        """
        quote_date = self.extract_session_date(quote)

        # Causal month-end detection fallback via MonthlyRebalanceStrategy
        decision_date = self.rebalance_strategy.step(quote)
        if (
            decision_date is not None
            and decision_date not in self._rebalance_plans_by_decision_date
            and not self.has_pending_orders()
        ):
            logger.info(
                "Causal month transition detected on opening quote %s (%s): planning rebalance for session T=%s",
                quote.instrument_id,
                quote_date.strftime("%Y-%m-%d"),
                decision_date.strftime("%Y-%m-%d"),
            )
            self._plan_and_queue_rebalance(decision_date)

        super().on_quote(quote)

    def _get_fresh_model(self) -> BaseModel:
        """Instantiates a fresh model instance to avoid carryover state between folds."""
        if hasattr(self.model_prototype, "params") and hasattr(
            type(self.model_prototype), "__init__"
        ):
            return type(self.model_prototype)(**self.model_prototype.params)
        return copy.deepcopy(self.model_prototype)

    def _plan_and_queue_rebalance(self, decision_date: pd.Timestamp) -> None:
        """
        Prepares lookahead-free ML predictions, target weights, and planned order instructions
        using strictly data available through session T close (decision_date).
        Queues planned orders for instrument-by-instrument release at session T+1 open.
        """
        self._rebalance_plans_by_decision_date.add(decision_date)

        # Step 1: Check minimum training history
        date_idx = self.date_to_index.get(decision_date)
        if date_idx is None:
            past_dates = [d for d in self.all_trading_dates if d <= decision_date]
            date_idx = len(past_dates) - 1

        if date_idx < self.min_train_days:
            logger.info(
                "Skipping rebalance at %s: elapsed trading days (%d) < min_train_days (%d)",
                decision_date.strftime("%Y-%m-%d"),
                date_idx,
                self.min_train_days,
            )
            return

        # Step 2: Determine training cutoff date (Lookahead prevention)
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
            "Training pooled XGBoost model for rebalance %s (cutoff: %s, train rows: %d)...",
            decision_date.strftime("%Y-%m-%d"),
            train_cutoff_date.strftime("%Y-%m-%d"),
            len(train_subset),
        )
        model = self._get_fresh_model()
        model.train(X_train, y_train)

        # Step 3: Feature extraction and prediction on decision_date
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
        self.predictions_by_date[decision_date] = selection_df

        # Step 4: Selection and Portfolio Construction
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

        self.rebalance_dates.append(decision_date)
        self.selected_stocks_by_date[decision_date] = selected_symbols
        self.weights_by_date[decision_date] = target_weights_by_symbol

        # Step 5: Map target weights to InstrumentId
        target_weights: Dict[Union[InstrumentId, str], float] = {}
        for sym, w in target_weights_by_symbol.items():
            sym_clean = sym.strip().upper()
            inst_id = self._symbol_to_instrument_id.get(sym_clean)
            if inst_id is not None:
                target_weights[inst_id] = w
            else:
                target_weights[f"{sym_clean}.{self.venue}"] = w

        # Reference prices: strictly as of session T close
        prices_for_rebalancer: Dict[Union[InstrumentId, str], float] = dict(self._latest_prices)

        # Fallback reference prices from current_bar_data
        for _, row in valid_current.iterrows():
            sym_str = str(row["Symbol"]).strip().upper()
            inst_id = self._symbol_to_instrument_id.get(sym_str)
            if inst_id is not None and inst_id not in prices_for_rebalancer:
                if "Close" in row and not pd.isna(row["Close"]) and float(row["Close"]) > 0:
                    prices_for_rebalancer[inst_id] = float(row["Close"])

        # Step 6: Plan Rebalance (Decoupled from execution)
        logger.info(
            "Planning Rebalance for %d target symbols (Total Weight: %.4f) at session T close (%s)...",
            len(target_weights),
            sum(target_weights.values()),
            decision_date.strftime("%Y-%m-%d"),
        )
        plan = self.rebalancer.plan_rebalance(
            target_weights=target_weights,
            reference_prices=prices_for_rebalancer,
            decision_date=decision_date,
        )

        self.orders_by_date[decision_date] = plan.all_orders

        # Delegate queueing to base strategy method
        self.queue_rebalance_plan(plan)

    def _execute_rebalance(
        self,
        decision_date: pd.Timestamp,
        session_prices: Optional[Dict[InstrumentId, float]] = None,
    ) -> None:
        """Compatibility wrapper that triggers rebalance planning for decision_date."""
        self._plan_and_queue_rebalance(decision_date)

    @classmethod
    def run(
        cls,
        data_source: Union[str, pathlib.Path, pd.DataFrame] = "data/raw/Final_NIFTY50_EoD_Data",
        symbol: Optional[str] = None,
        venue: str = "NSE",
        currency: str = "INR",
        starting_balance: float = 10_000_000.0,
        pipeline: Optional[StrategyDataPipeline] = None,
        engine_config: Optional[BacktestEngineConfig] = None,
        config: Optional[XGBoostRegressorConfig] = None,
        feature_columns: Optional[List[str]] = None,
        target_horizon: int = 10,
        target_column: str = TARGET_COLUMN,
        min_train_days: int = 252,
        top_n: int = 20,
        fee_model: Optional[FeeModel] = None,
        fill_model: Optional[FillModel] = None,
        ordinary_charges_config: Optional[OrdinaryChargesConfig] = None,
        dp_charges_config: Optional[DPChargesConfig] = None,
        slippage_pct: float = 0.0005,
        enable_slippage: bool = True,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        unique_id: str = "001",
        **kwargs: Any,
    ) -> BacktestEngine:
        """
        Executes a real multi-instrument NautilusTrader backtest for XGBoostRegressorStrategy.

        Flow:
            Directory of CSVs (or single CSV / DataFrame)
              -> For each source: StrategyDataPipeline.prepare() with strict validation
              -> Strictly skip invalid files and log validation error
              -> Register each valid Equity with BacktestEngine
              -> Add all Bar streams to BacktestEngine
              -> Construct pooled training and feature datasets across valid universe
              -> Sort all bars chronologically
              -> Create ONE XGBoostRegressorStrategy managing the entire universe
              -> Run ONE shared cash account / portfolio
              -> Return completed BacktestEngine
        """
        items_to_process: List[Tuple[Union[str, pathlib.Path, pd.DataFrame], Optional[str]]] = []

        if isinstance(data_source, pd.DataFrame):
            items_to_process.append((data_source, symbol))
        else:
            p = pathlib.Path(data_source)
            if p.is_file():
                sym = symbol or p.stem.strip().upper()
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

        pipe = pipeline or StrategyDataPipeline()

        # Initialize per-run storage and manifest
        run_store = RunStore(run_id=unique_id, base_dir="strategy_results")
        run_dir = run_store.create_run_dir()

        cfg = engine_config or BacktestEngineConfig(
            trader_id=TraderId.from_str("TESTER-001"),
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
        engine = BacktestEngine(config=cfg)

        # Instantiate ExecutionEventRecorder to coordinate fill timestamps and reference prices
        recorder = ExecutionEventRecorder(exchange_tz="Asia/Kolkata")

        active_fee_model = fee_model
        if active_fee_model is None:
            active_fee_model = IndianEquityDeliveryFeeModel(
                ordinary_config=ordinary_charges_config,
                dp_config=dp_charges_config,
                recorder=recorder,
            )
        elif hasattr(active_fee_model, "set_recorder") and getattr(active_fee_model, "recorder", None) is None:
            active_fee_model.set_recorder(recorder)

        active_fill_model = fill_model
        if active_fill_model is None:
            active_fill_model = AdverseSlippageFillModel(
                config=AdverseSlippageConfig(
                    slippage_pct=slippage_pct,
                    enabled=enable_slippage,
                ),
                recorder=recorder,
            )
        elif hasattr(active_fill_model, "set_recorder") and getattr(active_fill_model, "recorder", None) is None:
            active_fill_model.set_recorder(recorder)

        engine.add_venue(
            venue=Venue(venue),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(starting_balance, Currency.from_str(currency))],
            fee_model=active_fee_model,
            fill_model=active_fill_model,
            latency_model=StaticLatencyModel(insert_latency_nanos=1_000_000),
        )

        bar_types: List[BarType] = []
        featured_dfs: List[pd.DataFrame] = []
        skipped_files: List[Tuple[str, Optional[str], str]] = []
        all_bars_loaded: List[Bar] = []
        all_quotes_loaded: List[QuoteTick] = []

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
            all_bars_loaded.extend(prepared.bars)
            if prepared.opening_quotes:
                engine.add_data(prepared.opening_quotes)
                all_quotes_loaded.extend(prepared.opening_quotes)
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

        # Export loaded market data
        MarketDataExporter.export(
            bars=all_bars_loaded,
            opening_quotes=all_quotes_loaded,
            run_dir=run_dir,
        )

        # Build pooled datasets across valid universe
        combined_featured_df = pd.concat(featured_dfs, ignore_index=True)
        combined_featured_df["Date"] = pd.to_datetime(combined_featured_df["Date"])
        combined_featured_df = combined_featured_df.sort_values(
            by=["Symbol", "Date"]
        ).reset_index(drop=True)

        target_eng = TargetEngine(horizon=target_horizon, target_col=target_column)
        targeted_df = target_eng.transform(combined_featured_df)
        targeted_df["Date"] = pd.to_datetime(targeted_df["Date"])

        f_cols = list(feature_columns or FEATURE_COLUMNS)
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

        # Initialize metadata manifest
        loaded_symbols = [str(bt.instrument_id.symbol) for bt in bar_types]
        config_summary = {
            "data_source": str(data_source),
            "symbol": symbol,
            "starting_balance": starting_balance,
            "venue": venue,
            "currency": currency,
            "target_horizon": target_horizon,
            "target_column": target_column,
            "min_train_days": min_train_days,
            "top_n": top_n,
            "start_date": start_date,
            "end_date": end_date,
            "enable_slippage": enable_slippage,
            "slippage_pct": slippage_pct,
        }
        run_store.init_metadata(symbols=loaded_symbols, config=config_summary)

        engine.sort_data()

        strat_config = config or XGBoostRegressorConfig(
            bar_types=bar_types,
            featured_df=combined_featured_df,
            training_df=training_df,
            all_trading_dates=all_trading_dates,
            instruments_by_date=instruments_by_date,
            feature_columns=f_cols,
            target_horizon=target_horizon,
            target_column=target_column,
            min_train_days=min_train_days,
            top_n=top_n,
            venue=venue,
            currency=currency,
            fee_model=active_fee_model,
            fill_model=active_fill_model,
            recorder=recorder,
            ordinary_charges_config=ordinary_charges_config,
            dp_charges_config=dp_charges_config,
            slippage_pct=slippage_pct,
            enable_slippage=enable_slippage,
            **kwargs,
        )
        strategy = cls(config=strat_config)
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

        try:
            if start_ts is not None and end_ts is not None:
                engine.run(start=start_ts, end=end_ts)
            elif start_ts is not None:
                engine.run(start=start_ts)
            elif end_ts is not None:
                engine.run(end=end_ts)
            else:
                engine.run()

            # Post-run report and decision exports
            NautilusExporter.export(
                engine=engine,
                run_dir=run_dir,
                venue=venue,
                currency=currency,
            )
            StrategyExporter.export(
                strategy=strategy,
                run_dir=run_dir,
            )
            CostExporter.export(
                cost_report=strategy.get_cost_report(),
                fee_model=active_fee_model if isinstance(active_fee_model, IndianEquityDeliveryFeeModel) else None,
                recorder=recorder,
                run_dir=run_dir,
            )
            run_store.complete_metadata()
            return engine
        except Exception as exc:
            run_store.fail_metadata(error=str(exc))
            raise


if __name__ == "__main__":

    configure_logging()
    data_source = "NIFTY200"
    logger.info("Starting XGBoost Regressor Strategy Backtest...")
    unique_id = data_source + "_" + pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")

    engine = XGBoostRegressorStrategy.run(
        data_source=f"data/raw/{data_source}",
        starting_balance=10_000_00.0,
        top_n=20,
        min_train_days=252*5,
        unique_id=unique_id,
        start_date="2015-01-01",
    )

    logger.info("=== Multi-Instrument XGBoost Regressor Backtest Completed: %s ===", unique_id)