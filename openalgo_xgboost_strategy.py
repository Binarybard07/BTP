#!/usr/bin/env python
r"""
openalgo/strategies/scripts/openalgo_xgboost_strategy.py
========================================================
Production OpenAlgo Paper-Trading Strategy for BTP XGBoost Regressor.

Lifecycle & Schedule:
  - Scheduled to start at 19:00 (7:00 PM IST) and stop at 16:00 (4:00 PM IST) next day.
  - On start / evening:
      Consumes updated adjusted CSVs, fits XGBoost model, generates cross-sectional forecasts,
      evaluates monthly rebalance eligibility, and stages target weights as 'PENDING_MARKET_OPEN'.
  - At 09:15:00 AM IST next morning (Market Open):
      Verifies OpenAlgo Analyzer mode safety gate, fetches live tradable prices (LTP) and portfolio funds,
      submits CNC SELL orders first, followed by CNC BUY orders.
"""
from __future__ import annotations

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
from typing import Any, Dict, List, Optional, Set, Tuple

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
# even when OpenAlgo launches the process with cwd set to 'openalgo'
os.chdir(BTP_ROOT)

# Set absolute data paths so loader.py and config.py always find the datasets
os.environ["QBEAST_DATA_ROOT"] = str((BTP_ROOT / "data" / "Final_With_Survivorship_Data").resolve())
os.environ["QBEAST_MEMBERSHIP_ROOT"] = str((BTP_ROOT / "data" / "Survivorship_Bias").resolve())

# Add BTP modules to sys.path
sys.path.insert(0, str(BTP_ROOT / "src"))
sys.path.insert(0, str(BTP_ROOT / "Backtest_Module" / "src"))

# Bypass Nautilus interpreter check while preserving config.py unchanged
os.environ["REGIMESWITCH_SKIP_PREFLIGHT"] = "1"

from dotenv import load_dotenv
load_dotenv(BTP_ROOT / ".env")

# Direct import of OpenAlgo API SDK
from openalgo import api as OpenAlgoAPI

# Import BTP research strategy components
from equity_strategy.data.training_dataset import TrainingDatasetBuilder
from equity_strategy.features.engine import FeatureEngine
from equity_strategy.models.xgboost_regressor import XGBoostRegressor
from equity_strategy.portfolio.equal_weight import EqualWeightPortfolio
from equity_strategy.risk_management.fixed_percentage import FixedPercentageRiskManager
from equity_strategy.selection.percentile import PercentileSelector
from equity_strategy.targets.engine import TargetEngine
from qbeast_backtest.contracts import MarketData
from qbeast_backtest.stage1_data.loader import load_market_data, rebalance_dates

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

UNIVERSE_NAME = os.environ.get("QBEAST_BACKTEST_UNIVERSE", "nifty50_ddq")
DATA_ROOT = BTP_ROOT / os.environ.get("QBEAST_DATA_ROOT", "data/Final_With_Survivorship_Data")
UNIVERSE_DATA_DIR = DATA_ROOT / "NIFTY50"

STATE_FILE = CURRENT_DIR / "openalgo_xgboost_state.json"

MIN_TRAIN_DAYS = 252
TARGET_HORIZON = 30
PERCENTILE = 0.85
MAX_DEPLOYMENT_PCT = 0.90

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
    "return_21": {"indicator": "returns", "params": {"period": 21}},
    "return_63": {"indicator": "returns", "params": {"period": 63}},
    "return_252": {"indicator": "returns", "params": {"period": 252}},
    "close_to_ma50": {"indicator": "close_to_sma", "params": {"period": 50}},
    "close_to_ma200": {"indicator": "close_to_sma", "params": {"period": 200}},
    "volatility_21": {"indicator": "rolling_std", "params": {"period": 21}},
}

# Timings (IST)
EVENING_RUN_HOUR = 21       # 9:00 PM IST
EVENING_RUN_MINUTE = 0

MARKET_OPEN_HOUR = 9        # 9:15:00 AM IST
MARKET_OPEN_MINUTE = 15
MARKET_OPEN_SECOND = 0

MARKET_CLOSE_HOUR = 16      # 4:00:00 PM IST


# ---------------------------------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------------------------------
def load_state() -> Dict[str, Any]:
    """Load persistent strategy state from disk."""
    if not STATE_FILE.exists():
        return {
            "last_pipeline_run_date": None,
            "last_order_execution_date": None,
            "last_decision_date": None,
            "pending_rebalance": None,
            "execution_history": [],
            "last_error": None,
            "last_updated": datetime.now(IST).isoformat(),
        }
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading state from {STATE_FILE}: {e}. Initializing fresh state.")
        return {
            "last_pipeline_run_date": None,
            "last_order_execution_date": None,
            "last_decision_date": None,
            "pending_rebalance": None,
            "execution_history": [],
            "last_error": str(e),
            "last_updated": datetime.now(IST).isoformat(),
        }


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
    selector = PercentileSelector(percentile=PERCENTILE)
    selected_symbols = selector.select(selection_df)
    if not selected_symbols:
        logger.info(f"Zero symbols selected by PercentileSelector on {t_ts.date()}. Returning cash.")
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


def check_is_monthly_rebalance(decision_date: pd.Timestamp, cal: pd.DatetimeIndex) -> bool:
    """Verifies if decision_date is the last trading session of its calendar month."""
    rebals = rebalance_dates(cal)
    return decision_date in rebals


def execute_evening_pipeline() -> Optional[Dict[str, Any]]:
    """
    Executes the evening strategy computation at 9:00 PM:
    1. Ingests historical adjusted CSV data directly.
    2. Builds features and forward target datasets.
    3. Trains model and predicts target weights on the latest session.
    4. Evaluates monthly rebalance eligibility.
    """
    logger.info(f"Starting evening 9:00 PM BTP XGBoost pipeline for universe '{UNIVERSE_NAME}'...")

    os.environ["QBEAST_BACKTEST_UNIVERSE"] = UNIVERSE_NAME
    md = load_market_data(verbose=False)

    # In paper trading, the latest session is fillable at the next morning open
    object.__setattr__(md, "last_session_fillable", True)

    closes = md.history_closes
    if closes is None or closes.empty:
        raise ValueError("md.history_closes is empty; cannot compute features.")

    # Identify the latest session where stock prices are present
    valid_stock_dates = md.history_closes.dropna(how="all").index
    available_dates = md.calendar[md.calendar.isin(valid_stock_dates)]
    latest_session = available_dates[-1] if len(available_dates) > 0 else md.calendar[-1]
    logger.info(f"Latest historical session: {latest_session.date()}")

    # 1. Transform wide-format historical closes to canonical long DataFrame
    long_df = closes.unstack().reset_index()
    long_df.columns = ["Symbol", "Date", "Close"]
    long_df = (
        long_df.dropna(subset=["Close"])
        .sort_values(by=["Symbol", "Date"])
        .reset_index(drop=True)
    )

    # 2. Compute technical features
    feature_engine = FeatureEngine(features=FEATURE_CONFIG)
    featured_df = feature_engine.transform(long_df)

    # 3. Build training dataset
    target_engine = TargetEngine(
        target_col="fwd_return_30",
        indicator="forward_return",
        params={"horizon": TARGET_HORIZON},
    )
    builder = TrainingDatasetBuilder(
        feature_engine=feature_engine,
        target_engine=target_engine,
        ohlcv_columns=["Close"],
    )
    full_dataset = builder.build(featured_df)

    # 4. Check monthly rebalance eligibility
    is_rebal = check_is_monthly_rebalance(latest_session, md.calendar)
    logger.info(f"Session {latest_session.date()} monthly rebalance eligible: {is_rebal}")

    # 5. Compute target weights for latest session
    target_weights = run_model_decision_for_date(
        t_ts=latest_session,
        md=md,
        featured_df=featured_df,
        full_dataset=full_dataset,
        feature_engine=feature_engine,
        target_engine=target_engine,
    )

    logger.info(f"Target weights calculated for {latest_session.date()}: {target_weights}")

    return {
        "decision_date": str(latest_session.date()),
        "is_rebalance": is_rebal,
        "target_weights": target_weights,
        "calculated_at": datetime.now(IST).isoformat(),
    }


# ---------------------------------------------------------------------------------------------------
# Phase 2: Market Open Execution via OpenAlgo Analyzer
# ---------------------------------------------------------------------------------------------------
def get_openalgo_client() -> OpenAlgoAPI:
    """Initialize OpenAlgo API client using injected credentials or defaults."""
    api_key = os.getenv("OPENALGO_API_KEY")
    if not api_key:
        raise RuntimeError("OPENALGO_API_KEY environment variable not set.")

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
    """Retrieves current stock positions across both settled holdings and intraday CNC positions."""
    portfolio_positions: Dict[str, int] = {}

    # 1. Settled holdings
    try:
        holdings_res = client.holdings()
        if holdings_res and holdings_res.get("status") == "success":
            for item in holdings_res.get("data", []):
                sym = str(item.get("symbol", "")).upper()
                qty = int(item.get("quantity", 0))
                if sym and qty != 0:
                    portfolio_positions[sym] = portfolio_positions.get(sym, 0) + qty
    except Exception as e:
        logger.warning(f"Could not retrieve holdings: {e}")

    # 2. Intraday CNC positions
    try:
        pos_res = client.positionbook()
        if pos_res and pos_res.get("status") == "success":
            for item in pos_res.get("data", []):
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
        if funds_res and funds_res.get("status") == "success":
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
        logger.error(f"Error fetching funds: {e}")

    logger.warning(f"Using fallback equity {fallback_default}")
    return fallback_default


def get_live_market_price(client: OpenAlgoAPI, symbol: str) -> Optional[float]:
    """Retrieves the real-time tradable market price (LTP) from OpenAlgo."""
    try:
        quote_res = client.quotes(symbol=symbol, exchange=EXCHANGE)
        if quote_res and quote_res.get("status") == "success":
            ltp = float(quote_res.get("data", {}).get("ltp", 0.0))
            if ltp > 0:
                return ltp
    except Exception as e:
        logger.debug(f"Error fetching live quote for {symbol}: {e}")
    return None


def execute_market_open_orders(
    client: OpenAlgoAPI,
    target_weights: Dict[str, float],
) -> Dict[str, Any]:
    """
    Executes the portfolio rebalance at Market Open (09:15:00 AM IST):
    1. Safety gate check.
    2. Fetches total account equity.
    3. Fetches existing portfolio quantities.
    4. Computes target share quantities using live market prices.
    5. Submits SELL orders first (CNC).
    6. Submits BUY orders second (CNC).
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

    # 1. Total equity
    total_equity = get_account_total_equity(client)
    logger.info(f"Total account equity for rebalance: Rs. {total_equity:,.2f}")

    # 2. Current holdings
    current_positions = get_current_positions_and_holdings(client)
    logger.info(f"Current portfolio positions: {current_positions}")

    # 3. Target shares calculation using live market prices
    all_symbols = set(target_weights.keys()) | set(current_positions.keys())
    target_quantities: Dict[str, int] = {}
    order_deltas: Dict[str, int] = {}

    for sym in all_symbols:
        weight = target_weights.get(sym, 0.0)
        curr_qty = current_positions.get(sym, 0)

        if weight > 0:
            ltp = get_live_market_price(client, sym)
            if not ltp or ltp <= 0:
                try:
                    df = pd.read_csv(UNIVERSE_DATA_DIR / f"{sym}.csv")
                    ltp = float(df["close"].dropna().iloc[-1])
                    logger.warning(f"Live quote unavailable for {sym}; using latest CSV close {ltp:.2f} for share sizing")
                except Exception:
                    logger.error(f"Cannot resolve price for {sym}; skipping symbol.")
                    continue

            target_val = weight * total_equity
            target_qty = int(math.floor(target_val / ltp))
            target_quantities[sym] = target_qty
        else:
            target_quantities[sym] = 0

        delta_qty = target_quantities[sym] - curr_qty
        if delta_qty != 0:
            order_deltas[sym] = delta_qty

    logger.info(f"Calculated rebalance order deltas: {order_deltas}")

    # 4. Phase A: Submit SELL orders first
    sell_orders = {sym: abs(delta) for sym, delta in order_deltas.items() if delta < 0}
    for sym, qty in sell_orders.items():
        logger.info(f"[SELL] Submitting CNC SELL order for {sym}: {qty} shares")
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
            logger.info(f"[SELL] Order response for {sym}: {res}")
            execution_report["sells"].append({"symbol": sym, "quantity": qty, "response": res})
        except Exception as e:
            err = f"Failed to submit SELL order for {sym}: {e}"
            logger.error(err)
            execution_report["errors"].append(err)

    if sell_orders:
        time.sleep(2)

    # 5. Phase B: Submit BUY orders second
    buy_orders = {sym: delta for sym, delta in order_deltas.items() if delta > 0}
    for sym, qty in buy_orders.items():
        logger.info(f"[BUY] Submitting CNC BUY order for {sym}: {qty} shares")
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
            logger.info(f"[BUY] Order response for {sym}: {res}")
            execution_report["buys"].append({"symbol": sym, "quantity": qty, "response": res})
        except Exception as e:
            err = f"Failed to submit BUY order for {sym}: {e}"
            logger.error(err)
            execution_report["errors"].append(err)

    return execution_report


# ---------------------------------------------------------------------------------------------------
# Strategy Daemon
# ---------------------------------------------------------------------------------------------------
class BTPStrategyDaemon:
    def __init__(self) -> None:
        self.running = True
        self.state = load_state()

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        logger.info(f"Signal {signum} received. Stopping strategy daemon cleanly...")
        self.running = False

    def is_market_open_time(self) -> bool:
        """Checks if current time is within trading session (09:15:00 to 16:00:00 IST on weekdays)."""
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return False

        market_start = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=MARKET_OPEN_SECOND, microsecond=0)
        market_end = now.replace(hour=MARKET_CLOSE_HOUR, minute=0, second=0, microsecond=0)
        return market_start <= now <= market_end

    def run_cycle(self) -> None:
        """Single evaluation cycle of the strategy daemon."""
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")

        # 1. Evening / Startup ML Model Calculation (Option A)
        # Executes immediately on startup outside market hours if not already executed today.
        # Once completed, it will NOT repeat on the same calendar day even across restarts.
        if not self.is_market_open_time() and self.state.get("last_pipeline_run_date") != today_str:
            logger.info(f"Strategy active for {today_str} (time: {now.strftime('%H:%M:%S IST')}). Executing model pipeline now...")
            try:
                pipeline_res = execute_evening_pipeline()
                if pipeline_res:
                    self.state["last_pipeline_run_date"] = today_str
                    self.state["last_decision_date"] = pipeline_res["decision_date"]

                    if pipeline_res.get("is_rebalance"):
                        logger.info(f"Eligible monthly rebalance on {pipeline_res['decision_date']}! Staged for 09:15:00 AM market open.")
                        self.state["pending_rebalance"] = {
                            "decision_date": pipeline_res["decision_date"],
                            "target_weights": pipeline_res["target_weights"],
                            "status": "PENDING_MARKET_OPEN",
                            "staged_at": datetime.now(IST).isoformat(),
                        }
                    else:
                        logger.info(f"Session {pipeline_res['decision_date']} is not a monthly rebalance date. Zero orders staged.")
                        self.state["pending_rebalance"] = None

                    save_state(self.state)
            except Exception as e:
                logger.exception(f"Error during model pipeline execution: {e}")
                self.state["last_error"] = str(e)
                save_state(self.state)

        # 2. Morning 09:15:00 AM Market Open Execution
        # Executes once per calendar day at Market Open
        pending = self.state.get("pending_rebalance")
        if (
            pending
            and pending.get("status") == "PENDING_MARKET_OPEN"
            and self.state.get("last_order_execution_date") != today_str
        ):
            if self.is_market_open_time():
                logger.info(f"Market open (>= 09:15:00 IST). Executing staged rebalance for {pending.get('decision_date')}...")
                try:
                    client = get_openalgo_client()
                    report = execute_market_open_orders(
                        client=client,
                        target_weights=pending.get("target_weights", {}),
                    )
                    pending["status"] = "COMPLETED"
                    pending["execution_report"] = report
                    pending["executed_at"] = datetime.now(IST).isoformat()
                    self.state["execution_history"].append(copy.deepcopy(pending))
                    self.state["last_order_execution_date"] = today_str
                    self.state["pending_rebalance"] = None
                    save_state(self.state)
                    logger.info("Market Open rebalance execution successfully completed.")
                except Exception as e:
                    logger.exception(f"Error executing rebalance: {e}")
                    self.state["last_error"] = str(e)
                    save_state(self.state)

    def run(self, poll_interval_seconds: int = 15) -> None:
        """Main continuous strategy loop."""
        logger.info(f"BTP XGBoost Strategy Daemon active. Polling every {poll_interval_seconds}s...")
        while self.running:
            try:
                self.run_cycle()
            except Exception as e:
                logger.exception(f"Unhandled error in main loop: {e}")
            time.sleep(poll_interval_seconds)
        logger.info("Strategy daemon stopped cleanly.")


def main() -> int:
    daemon = BTPStrategyDaemon()
    daemon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
