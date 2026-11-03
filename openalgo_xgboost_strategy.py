#!/usr/bin/env python
r"""
openalgo_xgboost_strategy.py
============================
Production OpenAlgo Paper-Trading Strategy Daemon for BTP XGBoost Regressor.

Lifecycle & Architecture:
  1. Evening Pipeline (Outside Market Hours / ~9:00 PM IST):
     - Evaluates whether today is the last trading day of the calendar month (using OpenAlgo Market Calendar).
     - Fits the expanding-window XGBoost model on historical data.
     - Forecasts 63-day forward returns for the point-in-time eligible universe (nifty200_ddq).
     - Constructs the Top-10 equal-weight portfolio (95% capital deployed, 5% cash reserve).
     - Pre-calculates target share quantities using latest CSV close prices.
     - Fetches current OpenAlgo portfolio positions/holdings.
     - Pre-computes exact order deltas, SELL orders, and BUY orders.
     - Stages the rebalance as 'PENDING_MARKET_OPEN' in openalgo_xgboost_state.json.

  2. Morning Execution (09:15:00 AM IST Market Open):
     - Wakes up with sub-second precision at exactly 09:15:00.000 IST.
     - Confirms the OpenAlgo Analyzer (paper trading) safety gate.
     - Immediately dispatches pre-calculated SELL orders, then BUY orders.
     - Zero portfolio/quote queries at market open for maximum speed and zero slippage.
"""
from __future__ import annotations

import calendar
import copy
import json
import logging
import math
import os
import signal
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import pytz

# ---------------------------------------------------------------------------------------------------
# Setup Paths & Environment
# ---------------------------------------------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent
if (CURRENT_DIR / "data").exists():
    BTP_ROOT = CURRENT_DIR
elif len(CURRENT_DIR.parents) >= 3 and (CURRENT_DIR.parents[2] / "data").exists():
    BTP_ROOT = CURRENT_DIR.parents[2]
else:
    BTP_ROOT = Path("C:/Users/Jenish/Desktop/SEM VII/BTP").resolve()

# Switch working directory to BTP_ROOT so relative paths in BTP modules resolve correctly
os.chdir(BTP_ROOT)

# Set absolute data paths so loader.py and config.py always find the datasets
os.environ["QBEAST_DATA_ROOT"] = str((BTP_ROOT / "data" / "Final_With_Survivorship_Data").resolve())
os.environ["QBEAST_MEMBERSHIP_ROOT"] = str((BTP_ROOT / "data" / "Survivorship_Bias").resolve())

# Add BTP modules and OpenAlgo to sys.path
sys.path.insert(0, str(BTP_ROOT / "src"))
sys.path.insert(0, str(BTP_ROOT / "Backtest_Module" / "src"))
sys.path.insert(0, str((BTP_ROOT / "OpenAlgo").resolve()))

# Bypass Nautilus interpreter check while preserving config.py unchanged
os.environ["REGIMESWITCH_SKIP_PREFLIGHT"] = "1"

from dotenv import load_dotenv
load_dotenv(BTP_ROOT / ".env", override=True)

# Direct imports from OpenAlgo virtual environment & database
from openalgo import api as OpenAlgoAPI
from database.market_calendar_db import is_market_holiday

# BTP strategy components
from equity_strategy.data.training_dataset import TrainingDatasetBuilder
from equity_strategy.features.engine import FeatureEngine
from equity_strategy.models.xgboost_regressor import XGBoostRegressor
from equity_strategy.portfolio.equal_weight import EqualWeightPortfolio
from equity_strategy.risk_management.fixed_percentage import FixedPercentageRiskManager
from equity_strategy.selection.top_n import TopNSelector
from equity_strategy.targets.engine import TargetEngine
from qbeast_backtest.contracts import MarketData
from qbeast_backtest.stage1_data.loader import load_market_data
from qbeast_backtest import universes as _U

IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] [OpenAlgo_XGBoost] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("OpenAlgo_XGBoost_Strategy")

# ---------------------------------------------------------------------------------------------------
# Strategy Parameters
# ---------------------------------------------------------------------------------------------------
STRATEGY_NAME = "OpenAlgo_XGBoost_Paper"
EXCHANGE = "NSE"
PRODUCT = "CNC"
PRICE_TYPE = "MARKET"

UNIVERSE_NAME = os.environ.get("QBEAST_BACKTEST_UNIVERSE", "nifty200_ddq")
os.environ["QBEAST_BACKTEST_UNIVERSE"] = UNIVERSE_NAME
_u_preset = _U.get(UNIVERSE_NAME)
UNIVERSE_DATA_DIR = Path(_u_preset["source"])

STATE_FILE = CURRENT_DIR / "openalgo_xgboost_state.json"

MIN_TRAIN_DAYS = 1260
TARGET_HORIZON = 63
TARGET_COL = "target_fwd_return_63"
TOP_N = 10
MAX_DEPLOYMENT_PCT = 0.95

MODEL_PARAMS = {
    "n_estimators": 100,
    "max_depth": 3,
    "tree_method": "hist",
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "random_state": 42,
    "n_jobs": 2,
}

FEATURE_CONFIG = {
    "close_to_ema20": {"indicator": "close_to_ema", "params": {"period": 20}},
    "close_to_ema50": {"indicator": "close_to_ema", "params": {"period": 50}},
    "close_to_sma100": {"indicator": "close_to_sma", "params": {"period": 100}},
    "close_to_sma20": {"indicator": "close_to_sma", "params": {"period": 20}},
    "close_to_sma200": {"indicator": "close_to_sma", "params": {"period": 200}},
    "close_to_sma50": {"indicator": "close_to_sma", "params": {"period": 50}},
    "return_10": {"indicator": "returns", "params": {"period": 10}},
    "return_126": {"indicator": "returns", "params": {"period": 126}},
    "return_21": {"indicator": "returns", "params": {"period": 21}},
    "return_252": {"indicator": "returns", "params": {"period": 252}},
    "return_5": {"indicator": "returns", "params": {"period": 5}},
    "return_63": {"indicator": "returns", "params": {"period": 63}},
    "roc_21": {"indicator": "roc", "params": {"period": 21}},
    "volatility_10": {"indicator": "rolling_std", "params": {"period": 10}},
    "volatility_21": {"indicator": "rolling_std", "params": {"period": 21}},
    "volatility_63": {"indicator": "rolling_std", "params": {"period": 63}},
}

# Market Timings (IST)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_OPEN_SECOND = 0

MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30
MARKET_CLOSE_SECOND = 0


# ---------------------------------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------------------------------
def load_state() -> Dict[str, Any]:
    """Load persistent strategy state from disk."""
    default_state = {
        "last_pipeline_run_date": None,
        "last_order_execution_date": None,
        "last_decision_date": None,
        "pending_rebalance": None,
        "execution_history": [],
        "last_error": None,
        "last_updated": datetime.now(IST).isoformat(),
    }
    if not STATE_FILE.exists():
        return default_state

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading state from {STATE_FILE}: {e}. Resetting to initial state.")
        default_state["last_error"] = str(e)
        return default_state


def save_state(state: Dict[str, Any]) -> None:
    """Save persistent strategy state atomically."""
    state["last_updated"] = datetime.now(IST).isoformat()
    tmp_path = STATE_FILE.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, STATE_FILE)
    except Exception as e:
        logger.error(f"Failed to save state to {STATE_FILE}: {e}")
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


# ---------------------------------------------------------------------------------------------------
# OpenAlgo Client & Safety Gate
# ---------------------------------------------------------------------------------------------------
def get_openalgo_client() -> OpenAlgoAPI:
    """Initialize OpenAlgo API client using injected credentials or defaults."""
    api_key = os.getenv("OPENALGO_API_KEY")
    if not api_key:
        raise RuntimeError("OPENALGO_API_KEY environment variable not set in .env")

    host = os.getenv("HOST_SERVER", "http://127.0.0.1:5000")
    return OpenAlgoAPI(api_key=api_key, host=host)


def verify_analyzer_mode(client: OpenAlgoAPI) -> bool:
    """
    Safety Gate: Confirms that OpenAlgo is operating in Analyzer (Paper Trading) mode.
    Refuses order execution if in live trading mode.
    """
    try:
        status_res = client.analyzerstatus()
        if not status_res or status_res.get("status") != "success":
            logger.error(f"Failed to query OpenAlgo analyzer status: {status_res}")
            return False

        data = status_res.get("data", {})
        analyze_mode = data.get("analyze_mode", False)
        mode = data.get("mode", "")

        is_safe = bool(analyze_mode is True or mode == "analyze")
        if not is_safe:
            logger.critical(
                f"SAFETY GATE TRIGGERED: OpenAlgo is NOT in Analyzer mode (mode={mode}, analyze_mode={analyze_mode})! "
                f"Aborting execution."
            )
        return is_safe
    except Exception as e:
        logger.exception(f"Error checking analyzer status: {e}")
        return False


def get_current_positions_and_holdings(client: OpenAlgoAPI) -> Dict[str, int]:
    """Retrieves current stock positions across settled holdings and intraday CNC positions."""
    portfolio_positions: Dict[str, int] = {}

    # 1. Settled holdings
    try:
        holdings_res = client.holdings()
        if isinstance(holdings_res, dict) and holdings_res.get("status") == "success":
            for item in holdings_res.get("data", []):
                if isinstance(item, dict):
                    sym = str(item.get("symbol", "")).upper()
                    qty = int(item.get("quantity", 0))
                    if sym and qty != 0:
                        portfolio_positions[sym] = portfolio_positions.get(sym, 0) + qty
    except Exception as e:
        logger.warning(f"Could not retrieve holdings: {e}")

    # 2. Intraday CNC positions
    try:
        pos_res = client.positionbook()
        if isinstance(pos_res, dict) and pos_res.get("status") == "success":
            for item in pos_res.get("data", []):
                if isinstance(item, dict):
                    sym = str(item.get("symbol", "")).upper()
                    qty = int(item.get("quantity", 0))
                    if sym and qty != 0:
                        portfolio_positions[sym] = portfolio_positions.get(sym, 0) + qty
    except Exception as e:
        logger.warning(f"Could not retrieve positionbook: {e}")

    return portfolio_positions


def get_account_total_equity(client: OpenAlgoAPI, fallback_default: float = 10000000.0) -> float:
    """Retrieves total account portfolio equity (Cash + Margin + MTM)."""
    try:
        funds_res = client.funds()
        if isinstance(funds_res, dict) and funds_res.get("status") == "success":
            data = funds_res.get("data", {})
            avail_cash = float(data.get("availablecash", 0.0))
            used_margin = float(data.get("utiliseddebits", 0.0))
            unrealized_pnl = float(data.get("m2munrealized", 0.0))

            total_equity = avail_cash + used_margin + unrealized_pnl
            if total_equity > 0:
                return total_equity
            if avail_cash > 0:
                return avail_cash
    except Exception as e:
        logger.warning(f"Could not retrieve funds from OpenAlgo: {e}")

    logger.warning(f"Using default equity of Rs. {fallback_default:,.2f}")
    return fallback_default


# ---------------------------------------------------------------------------------------------------
# Model Decision Pipeline (BTP Strategy Logic)
# ---------------------------------------------------------------------------------------------------
def run_model_decision_for_date(
    t_ts: pd.Timestamp,
    md: MarketData,
    featured_df: pd.DataFrame,
    full_dataset: pd.DataFrame,
    feature_engine: FeatureEngine,
    target_engine: TargetEngine,
) -> Dict[str, float]:
    """
    Executes the exact BTP XGBoost Regressor model training, inference, ranking, and sizing
    for a single target decision date T.
    """
    full_calendar = md.history_closes.index
    cal_until_t = full_calendar[full_calendar <= t_ts]

    if len(cal_until_t) <= target_engine.horizon:
        logger.warning(f"Calendar history prior to {t_ts.date()} is shorter than horizon ({target_engine.horizon}). Returning cash.")
        return {}

    cutoff_date = cal_until_t[-target_engine.horizon]
    eligible_train = full_dataset[full_dataset["Date"] < cutoff_date]

    unique_train_days = eligible_train["Date"].nunique()
    if unique_train_days < MIN_TRAIN_DAYS:
        logger.warning(f"Insufficient training days ({unique_train_days} < {MIN_TRAIN_DAYS}) at {t_ts.date()}. Returning cash.")
        return {}

    feature_cols = feature_engine.feature_columns
    target_col = target_engine.target_col

    X_train = eligible_train[feature_cols]
    y_train = eligible_train[target_col]

    # Fit fresh XGBoost model on expanding historical window
    logger.info(f"Fitting XGBoost model on {len(X_train):,} samples across {unique_train_days} trading days...")
    model = XGBoostRegressor(**MODEL_PARAMS)
    model.train(X_train, y_train)

    # Point-in-time tradability filter
    elig = md.eligible.get(t_ts)
    if elig is None:
        logger.warning(f"No eligible tradability set published for {t_ts.date()}. Returning cash.")
        return {}

    current_bar_data = featured_df[
        (featured_df["Date"] == t_ts) & (featured_df["Symbol"].isin(elig))
    ].copy()

    valid_current = current_bar_data.dropna(subset=feature_cols)
    if valid_current.empty:
        logger.warning(f"No valid feature rows for eligible symbols on {t_ts.date()}. Returning cash.")
        return {}

    X_pred = valid_current[feature_cols]
    scores = model.predict(X_pred)

    selection_df = pd.DataFrame({
        "Symbol": valid_current["Symbol"].astype(str).values,
        "score": scores,
    })

    # Selection & Portfolio Construction
    selector = TopNSelector(n=TOP_N)
    selected_symbols = selector.select(selection_df)
    if not selected_symbols:
        logger.info(f"Zero symbols selected by TopNSelector on {t_ts.date()}. Returning cash.")
        return {}

    portfolio = EqualWeightPortfolio()
    raw_weights = portfolio.construct(selected_symbols)
    if not raw_weights:
        return {}

    risk_manager = FixedPercentageRiskManager(max_deployment_pct=MAX_DEPLOYMENT_PCT)
    deployment_fraction = risk_manager.calculate_deployment_budget(1.0)

    target_weights = {
        sym: float(w * deployment_fraction)
        for sym, w in raw_weights.items()
    }

    return target_weights


def is_last_trading_day_of_month(d: date) -> bool:
    """Verifies if `d` is the last trading session of its calendar month using OpenAlgo's Market Calendar."""
    if is_market_holiday(d, "NSE"):
        return False
    _, last_day = calendar.monthrange(d.year, d.month)
    return not any(
        not is_market_holiday(date(d.year, d.month, day), "NSE")
        for day in range(d.day + 1, last_day + 1)
    )


def get_last_completed_trading_day(ref_dt: datetime) -> date:
    """Returns the most recent completed trading session on or before ref_dt."""
    market_close = ref_dt.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    cur = ref_dt.date() if ref_dt >= market_close else ref_dt.date() - timedelta(days=1)
    while is_market_holiday(cur, "NSE"):
        cur -= timedelta(days=1)
    return cur


def get_next_trading_day(ref_date: date) -> date:
    """Returns the next upcoming trading day strictly after ref_date."""
    cur = ref_date + timedelta(days=1)
    while is_market_holiday(cur, "NSE"):
        cur += timedelta(days=1)
    return cur


def execute_evening_pipeline() -> Optional[Dict[str, Any]]:
    """
    Executes the evening strategy computation outside market hours:
    1. Determines latest completed trading day and the next upcoming trading day.
    2. Checks if the completed day is the last trading day of its month.
    3. If eligible, trains XGBoost model and calculates target weights.
    4. Pre-calculates exact share quantities using CSV close prices beforehand.
    5. Fetches current portfolio positions and pre-computes order deltas, SELL orders, and BUY orders.
    6. Stages pre-calculated orders for the target execution date at market open.
    """
    now = datetime.now(IST)
    last_trading_day = get_last_completed_trading_day(now)
    target_exec_date = get_next_trading_day(last_trading_day)

    if not is_last_trading_day_of_month(last_trading_day):
        logger.info(
            f"Latest completed session {last_trading_day} is not the last trading day of {last_trading_day.strftime('%B %Y')}. "
            f"No monthly rebalance scheduled."
        )
        return {
            "decision_date": str(last_trading_day),
            "target_execution_date": str(target_exec_date),
            "is_rebalance": False,
            "target_weights": {},
            "target_quantities": {},
            "target_prices": {},
            "order_deltas": {},
            "sell_orders": {},
            "buy_orders": {},
            "calculated_at": now.isoformat(),
        }

    logger.info(
        f"Monthly rebalance triggered! Completed session {last_trading_day} is month-end. "
        f"Target execution: {target_exec_date} 09:15:00 AM IST."
    )

    os.environ["QBEAST_BACKTEST_UNIVERSE"] = UNIVERSE_NAME
    md = load_market_data(verbose=False)

    # In paper trading, the latest session is fillable at the next morning open
    object.__setattr__(md, "last_session_fillable", True)

    closes = md.history_closes
    if closes is None or closes.empty:
        raise ValueError("md.history_closes is empty; cannot compute features.")

    # Identify the latest rebalance session where stock prices are present in the dataset
    valid_stock_dates = md.history_closes.dropna(how="all").index
    available_dates = md.calendar[md.calendar.isin(valid_stock_dates)]
    rebals = [d for d in md.rebalance_dates if d in available_dates]
    latest_session = rebals[-1] if len(rebals) > 0 else (available_dates[-1] if len(available_dates) > 0 else md.calendar[-1])
    logger.info(f"Target decision session: {latest_session.date()}")

    # 1. Transform wide-format historical closes to canonical long DataFrame
    long_df = closes.unstack().reset_index()
    long_df.columns = ["Symbol", "Date", "Close"]
    long_df = (
        long_df.dropna(subset=["Close"])
        .sort_values(by=["Symbol", "Date"])
        .reset_index(drop=True)
    )

    # 2. Compute technical features
    logger.info(f"Computing technical features for {long_df['Symbol'].nunique()} symbols...")
    feature_engine = FeatureEngine(features=FEATURE_CONFIG)
    featured_df = feature_engine.transform(long_df)

    # 3. Build training dataset
    target_engine = TargetEngine(
        target_col=TARGET_COL,
        indicator="forward_return",
        params={"horizon": TARGET_HORIZON},
    )
    builder = TrainingDatasetBuilder(
        feature_engine=feature_engine,
        target_engine=target_engine,
        ohlcv_columns=["Close"],
    )
    full_dataset = builder.build(featured_df)

    # 4. Compute target weights for latest session
    target_weights = run_model_decision_for_date(
        t_ts=latest_session,
        md=md,
        featured_df=featured_df,
        full_dataset=full_dataset,
        feature_engine=feature_engine,
        target_engine=target_engine,
    )
    logger.info(f"Target weights for {latest_session.date()}: {target_weights}")

    # 5. Pre-calculate target share quantities beforehand using latest CSV close prices
    total_equity = float(os.getenv("TOTAL_EQUITY", "10000000.0"))
    client = None
    try:
        client = get_openalgo_client()
        equity_val = get_account_total_equity(client, fallback_default=total_equity)
        if equity_val > 0:
            total_equity = equity_val
    except Exception as e:
        logger.info(f"OpenAlgo funds check omitted during pre-calculation: {e}")

    logger.info(f"Calculating target quantities (Total Equity: Rs. {total_equity:,.2f})...")

    target_quantities: Dict[str, int] = {}
    target_prices: Dict[str, float] = {}

    for sym, w in target_weights.items():
        if w <= 0:
            target_quantities[sym] = 0
            continue
        csv_px = 0.0
        if sym in closes.columns:
            valid_s = closes[sym].dropna()
            if not valid_s.empty:
                csv_px = float(valid_s.iloc[-1])
        if csv_px <= 0:
            try:
                csv_file = UNIVERSE_DATA_DIR / f"{sym}.csv"
                if csv_file.exists():
                    df_sym = pd.read_csv(csv_file)
                    csv_px = float(df_sym["close"].dropna().iloc[-1])
            except Exception:
                pass

        if csv_px > 0:
            target_val = w * total_equity
            qty = int(math.floor(target_val / csv_px))
            target_quantities[sym] = qty
            target_prices[sym] = csv_px
            logger.info(f"  {sym:<12} | Weight: {w:5.2%} | Close: Rs. {csv_px:>9.2f} | Target Qty: {qty:>5} shares (Rs. {qty * csv_px:>12,.2f})")
        else:
            logger.warning(f"Could not resolve CSV close price for {sym}; target quantity set to 0.")
            target_quantities[sym] = 0

    # 6. Pre-calculate order deltas, SELL orders, and BUY orders in the evening
    current_positions: Dict[str, int] = {}
    if client:
        current_positions = get_current_positions_and_holdings(client)
        logger.info(f"Current portfolio positions: {current_positions if current_positions else 'Empty / All Cash'}")

    all_symbols = set(target_quantities.keys()) | set(current_positions.keys())
    order_deltas: Dict[str, int] = {}
    for sym in sorted(all_symbols):
        delta = target_quantities.get(sym, 0) - current_positions.get(sym, 0)
        if delta != 0:
            order_deltas[sym] = delta

    sell_orders = {sym: abs(delta) for sym, delta in order_deltas.items() if delta < 0}
    buy_orders = {sym: delta for sym, delta in order_deltas.items() if delta > 0}

    logger.info(f"Pre-calculated order deltas: {order_deltas}")
    logger.info(f"Staged {len(sell_orders)} SELL orders and {len(buy_orders)} BUY orders for Market Open.")

    return {
        "decision_date": str(latest_session.date()),
        "target_execution_date": str(target_exec_date),
        "is_rebalance": True,
        "target_weights": target_weights,
        "target_quantities": target_quantities,
        "target_prices": target_prices,
        "order_deltas": order_deltas,
        "sell_orders": sell_orders,
        "buy_orders": buy_orders,
        "calculated_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------------------------------
# Phase 2: Market Open Execution via OpenAlgo Analyzer
# ---------------------------------------------------------------------------------------------------
def execute_market_open_orders(
    client: OpenAlgoAPI,
    pending_rebalance: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Executes the pre-calculated portfolio rebalance immediately at Market Open (09:15:00 AM IST):
    1. Safety gate check.
    2. Submits pre-calculated SELL orders first (CNC).
    3. Submits pre-calculated BUY orders second (CNC) immediately without sleep.
    """
    execution_report: Dict[str, Any] = {
        "timestamp": datetime.now(IST).isoformat(),
        "sells": [],
        "buys": [],
        "errors": [],
    }

    if not verify_analyzer_mode(client):
        err = "ABORTED: Analyzer mode safety gate failed."
        logger.critical(err)
        execution_report["errors"].append(err)
        return execution_report

    sell_orders: Dict[str, int] = pending_rebalance.get("sell_orders", {})
    buy_orders: Dict[str, int] = pending_rebalance.get("buy_orders", {})
    logger.info(f"Executing market open rebalance: {len(sell_orders)} SELL orders, {len(buy_orders)} BUY orders.")

    # 1. Phase A: Submit SELL orders first
    for sym, qty in sell_orders.items():
        logger.info(f"[SELL] Submitting CNC order: {qty} shares of {sym}...")
        try:
            res = client.placeorder(
                strategy=STRATEGY_NAME,
                symbol=sym,
                action="SELL",
                exchange=EXCHANGE,
                price_type=PRICE_TYPE,
                product=PRODUCT,
                quantity=qty,
            )
            order_id = res.get("orderid", "N/A") if isinstance(res, dict) else "N/A"
            status = res.get("status", "unknown") if isinstance(res, dict) else "unknown"
            logger.info(f"[SELL] {sym} -> Status: {status} | Order ID: {order_id}")
            execution_report["sells"].append({"symbol": sym, "quantity": qty, "response": res})
        except Exception as e:
            err = f"Failed to submit SELL order for {sym}: {e}"
            logger.error(err)
            execution_report["errors"].append(err)

    # 2. Phase B: Submit BUY orders second immediately (no delay)
    for sym, qty in buy_orders.items():
        logger.info(f"[BUY] Submitting CNC order: {qty} shares of {sym}...")
        try:
            res = client.placeorder(
                strategy=STRATEGY_NAME,
                symbol=sym,
                action="BUY",
                exchange=EXCHANGE,
                price_type=PRICE_TYPE,
                product=PRODUCT,
                quantity=qty,
            )
            order_id = res.get("orderid", "N/A") if isinstance(res, dict) else "N/A"
            status = res.get("status", "unknown") if isinstance(res, dict) else "unknown"
            logger.info(f"[BUY] {sym} -> Status: {status} | Order ID: {order_id}")
            execution_report["buys"].append({"symbol": sym, "quantity": qty, "response": res})
        except Exception as e:
            err = f"Failed to submit BUY order for {sym}: {e}"
            logger.error(err)
            execution_report["errors"].append(err)

    logger.info(
        f"Market Open Rebalance Summary: "
        f"{len(execution_report['sells'])} SELLs placed, "
        f"{len(execution_report['buys'])} BUYs placed, "
        f"{len(execution_report['errors'])} errors."
    )
    return execution_report


# ---------------------------------------------------------------------------------------------------
# Strategy Daemon
# ---------------------------------------------------------------------------------------------------
class BTPStrategyDaemon:
    def __init__(self) -> None:
        self.running = True
        self.state = load_state()
        self.last_status_log_ts = 0.0

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        logger.info(f"Signal {signum} received. Stopping strategy daemon cleanly...")
        self.running = False

    def is_market_open_time(self) -> bool:
        """Checks if current time is within trading session (09:15:00 to 15:30:00 IST on trading days)."""
        now = datetime.now(IST)
        if is_market_holiday(now.date(), "NSE"):
            return False

        market_start = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=MARKET_OPEN_SECOND, microsecond=0)
        market_end = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=MARKET_CLOSE_SECOND, microsecond=0)
        return market_start <= now <= market_end

    def log_status(self, force: bool = False) -> None:
        """Logs a concise, useful summary of current strategy mode and market state."""
        now = datetime.now(IST)
        now_ts = now.timestamp()
        if not force and (now_ts - self.last_status_log_ts) < 1800:
            return

        self.last_status_log_ts = now_ts
        today_date = now.date()
        is_open = self.is_market_open_time()
        pending = self.state.get("pending_rebalance")
        last_completed = get_last_completed_trading_day(now)
        next_trading = get_next_trading_day(last_completed)

        if is_open:
            market_desc = "OPEN (09:15 - 15:30 IST)"
        elif is_market_holiday(today_date, "NSE"):
            market_desc = f"CLOSED (Weekend / Holiday: {now.strftime('%A')})"
        elif now.hour < MARKET_OPEN_HOUR or (now.hour == MARKET_OPEN_HOUR and now.minute < MARKET_OPEN_MINUTE):
            market_desc = "CLOSED (Pre-market before 09:15 IST)"
        else:
            market_desc = "CLOSED (Post-market after 15:30 IST)"

        if pending and pending.get("status") == "PENDING_MARKET_OPEN":
            target_date = pending.get("target_execution_date", str(next_trading))
            n_sells = len(pending.get("sell_orders", {}))
            n_buys = len(pending.get("buy_orders", {}))
            mode_desc = f"STAGED_REBALANCE (Target: {target_date} 09:15 IST | {n_sells} SELLs, {n_buys} BUYs)"
        else:
            mode_desc = f"IDLE (Last session: {last_completed} | Next session: {next_trading})"

        logger.info(f"[Strategy State] Market: {market_desc} | Mode: {mode_desc}")

    def wait_until_market_open(self, market_start: datetime) -> None:
        """Precision sub-second wait until exactly 09:15:00.000 IST."""
        diff = (market_start - datetime.now(IST)).total_seconds()
        if diff > 0.5:
            time.sleep(diff - 0.2)
        while self.running and datetime.now(IST) < market_start:
            time.sleep(0.005)

    def run_cycle(self) -> None:
        """Single evaluation cycle of the strategy daemon."""
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")
        market_start = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=MARKET_OPEN_SECOND, microsecond=0)

        # 1. Evening / Startup ML Model Calculation
        # Runs outside market hours if no pending rebalance is staged and not run today
        has_pending = bool(self.state.get("pending_rebalance") and self.state["pending_rebalance"].get("status") == "PENDING_MARKET_OPEN")
        if not self.is_market_open_time() and not has_pending and self.state.get("last_pipeline_run_date") != today_str:
            logger.info(f"Strategy active for {today_str} ({now.strftime('%H:%M:%S IST')}). Checking rebalance...")
            try:
                pipeline_res = execute_evening_pipeline()
                if pipeline_res:
                    self.state["last_pipeline_run_date"] = today_str
                    self.state["last_decision_date"] = pipeline_res["decision_date"]

                    if pipeline_res.get("is_rebalance"):
                        target_exec = pipeline_res["target_execution_date"]
                        logger.info(f"Monthly rebalance staged for {target_exec} 09:15:00 AM market open.")
                        self.state["pending_rebalance"] = {
                            "decision_date": pipeline_res["decision_date"],
                            "target_execution_date": target_exec,
                            "target_weights": pipeline_res["target_weights"],
                            "target_quantities": pipeline_res.get("target_quantities", {}),
                            "target_prices": pipeline_res.get("target_prices", {}),
                            "order_deltas": pipeline_res.get("order_deltas", {}),
                            "sell_orders": pipeline_res.get("sell_orders", {}),
                            "buy_orders": pipeline_res.get("buy_orders", {}),
                            "status": "PENDING_MARKET_OPEN",
                            "staged_at": now.isoformat(),
                        }
                    else:
                        self.state["pending_rebalance"] = None

                    save_state(self.state)
                    self.log_status(force=True)
            except Exception as e:
                logger.exception(f"Error during model pipeline execution: {e}")
                self.state["last_error"] = str(e)
                save_state(self.state)

        # 2. Morning 09:15:00 AM Market Open Execution
        pending = self.state.get("pending_rebalance")
        if (
            pending
            and pending.get("status") == "PENDING_MARKET_OPEN"
            and self.state.get("last_order_execution_date") != today_str
        ):
            target_exec_date = pending.get("target_execution_date")
            if target_exec_date and target_exec_date < today_str:
                logger.warning(f"Discarding expired pending rebalance for {target_exec_date}. No orders will be placed.")
                self.state["pending_rebalance"] = None
                save_state(self.state)
                pending = None

            if pending and target_exec_date == today_str and not is_market_holiday(now.date(), "NSE"):
                # Precision countdown if within 30 seconds of market open
                if now < market_start:
                    secs_left = (market_start - now).total_seconds()
                    if secs_left <= 30:
                        logger.info(f"Pending rebalance ready. Precision-waiting {secs_left:.2f}s for 09:15:00.000 IST...")
                        self.wait_until_market_open(market_start)
                        now = datetime.now(IST)

                if self.is_market_open_time():
                    logger.info(f"Market open reached ({now.strftime('%H:%M:%S.%f')[:12]} IST). Placing orders immediately...")
                    try:
                        client = get_openalgo_client()
                        report = execute_market_open_orders(
                            client=client,
                            pending_rebalance=pending,
                        )
                        pending["status"] = "COMPLETED"
                        pending["execution_report"] = report
                        pending["executed_at"] = datetime.now(IST).isoformat()
                        self.state["execution_history"].append(copy.deepcopy(pending))
                        self.state["last_order_execution_date"] = today_str
                        self.state["pending_rebalance"] = None
                        save_state(self.state)
                        logger.info("Market Open rebalance successfully completed.")
                        self.log_status(force=True)
                    except Exception as e:
                        logger.exception(f"Error executing rebalance: {e}")
                        self.state["last_error"] = str(e)
                        save_state(self.state)

    def run(self, poll_interval_seconds: int = 15) -> None:
        """Main continuous strategy loop with adaptive sub-second precision near 09:15:00."""
        logger.info(f"BTP XGBoost Strategy Daemon started. Universe: '{UNIVERSE_NAME}' | Strategy: '{STRATEGY_NAME}'")
        self.log_status(force=True)

        while self.running:
            try:
                self.run_cycle()
            except Exception as e:
                logger.exception(f"Unhandled error in main loop: {e}")

            now = datetime.now(IST)
            today_str = now.strftime("%Y-%m-%d")
            market_start = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=MARKET_OPEN_SECOND, microsecond=0)
            pending = self.state.get("pending_rebalance")

            # Periodic status check-in every 30 mins
            self.log_status()

            if (
                pending
                and pending.get("status") == "PENDING_MARKET_OPEN"
                and pending.get("target_execution_date") == today_str
                and not is_market_holiday(now.date(), "NSE")
                and now < market_start
            ):
                secs_to_open = (market_start - now).total_seconds()
                sleep_time = 0.5 if secs_to_open <= 30 else (2.0 if secs_to_open <= 120 else min(poll_interval_seconds, max(1.0, secs_to_open - 30)))
            else:
                sleep_time = poll_interval_seconds

            time.sleep(sleep_time)
        logger.info("Strategy daemon stopped cleanly.")


def main() -> int:
    daemon = BTPStrategyDaemon()
    daemon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
