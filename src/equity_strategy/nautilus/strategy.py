import logging
from typing import Dict, Any, List, Set, Optional
import pandas as pd

from nautilus_trader.trading import Strategy, StrategyConfig
from nautilus_trader.model import (
    Bar, BarType, BarSpecification, BarAggregation, PriceType,
    Symbol, Venue, InstrumentId, OrderSide, Quantity
)

logger = logging.getLogger(__name__)

class TargetWeightStrategyConfig(StrategyConfig):
    """Configuration for TargetWeightStrategy."""
    venue_name: str = "NSE"
    starting_capital: float = 1_000_000.0

class TargetWeightStrategy(Strategy):
    """
    NautilusTrader Strategy that consumes research target weight schedules.
    At each rebalance EffectiveDate (T+1), adjusts portfolio positions to target weights.
    """

    def __init__(self, config: TargetWeightStrategyConfig):
        super().__init__(config=config)
        self.venue = Venue(config.venue_name)
        self.starting_capital = config.starting_capital
        self.schedule: Dict[str, Dict[str, float]] = {}
        self.rebalanced_dates: Set[str] = set()
        self.bars_by_symbol: Dict[str, Bar] = {}
        self.bar_types_by_symbol: Dict[str, BarType] = {}

    def set_target_weights(self, target_weights_df: pd.DataFrame):
        """Processes target weights schedule: EffectiveDate -> Dict[Symbol, TargetWeight]."""
        self.schedule.clear()
        if target_weights_df is not None and not target_weights_df.empty:
            for eff_date, group in target_weights_df.groupby("EffectiveDate"):
                date_str = pd.Timestamp(eff_date).strftime("%Y-%m-%d")
                self.schedule[date_str] = dict(zip(group["Symbol"], group["TargetWeight"]))
        logger.info(f"Loaded schedule with {len(self.schedule)} monthly rebalance sessions.")

    def register_instrument_bar_type(self, symbol_str: str, bar_type: BarType):
        """Registers symbol to BarType mapping for subscription."""
        self.bar_types_by_symbol[symbol_str] = bar_type

    def on_start(self):
        """Subscribe to daily bars for all configured instruments."""
        for bar_type in self.bar_types_by_symbol.values():
            self.subscribe_bars(bar_type)
        logger.info(f"TargetWeightStrategy started. Total rebalance sessions in schedule: {len(self.schedule)}")

    def on_bar(self, bar: Bar):
        """Handle incoming daily bar events."""
        symbol_str = bar.bar_type.instrument_id.symbol.value
        self.bars_by_symbol[symbol_str] = bar

        # Convert timestamp to date string
        dt = pd.Timestamp(bar.ts_event, unit="ns").strftime("%Y-%m-%d")

        # Check if today is a rebalance EffectiveDate and hasn't been rebalanced yet
        if dt in self.schedule and dt not in self.rebalanced_dates:
            self._execute_rebalance(dt)
            self.rebalanced_dates.add(dt)

    def _execute_rebalance(self, date_str: str):
        """Executes portfolio rebalancing to target weights for the given EffectiveDate."""
        target_weights = self.schedule.get(date_str, {})
        logger.info(f"[{date_str}] Executing portfolio rebalance for {len(target_weights)} target holdings...")

        # Calculate current total portfolio equity
        try:
            total_equity_val = float(self.portfolio.equity(self.venue))
            if total_equity_val <= 0:
                total_equity_val = self.starting_capital
        except Exception:
            total_equity_val = self.starting_capital

        # 1. Close positions for symbols no longer in target weights
        for symbol_str, bar_type in list(self.bar_types_by_symbol.items()):
            if symbol_str not in target_weights:
                inst_id = bar_type.instrument_id
                net_pos = self.portfolio.net_position(inst_id)
                if net_pos is not None and float(net_pos) > 0:
                    order = self.order_factory.market(
                        instrument_id=inst_id,
                        order_side=OrderSide.SELL,
                        quantity=Quantity.from_int(int(net_pos)),
                    )
                    self.submit_order(order)
                    logger.info(f"[{date_str}] Submitted exit order for {symbol_str} ({int(net_pos)} shares)")

        # 2. Adjust or open positions for target weight holdings
        for symbol_str, target_weight in target_weights.items():
            if symbol_str not in self.bar_types_by_symbol:
                continue

            bar_type = self.bar_types_by_symbol[symbol_str]
            inst_id = bar_type.instrument_id
            latest_bar = self.bars_by_symbol.get(symbol_str)

            if latest_bar is None:
                continue

            close_price = float(latest_bar.close)
            if close_price <= 0:
                continue

            target_value = total_equity_val * target_weight
            target_qty = int(target_value / close_price)

            net_pos = self.portfolio.net_position(inst_id)
            current_qty = int(net_pos) if net_pos is not None else 0

            diff_qty = target_qty - current_qty

            if diff_qty > 0:
                order = self.order_factory.market(
                    instrument_id=inst_id,
                    order_side=OrderSide.BUY,
                    quantity=Quantity.from_int(diff_qty),
                )
                self.submit_order(order)
            elif diff_qty < 0:
                order = self.order_factory.market(
                    instrument_id=inst_id,
                    order_side=OrderSide.SELL,
                    quantity=Quantity.from_int(abs(diff_qty)),
                )
                self.submit_order(order)
