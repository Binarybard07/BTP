import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import pandas as pd

from nautilus_trader.common import Cache, OrderFactory
from nautilus_trader.model import (
    Currency,
    Equity,
    InstrumentId,
    MarketOrder,
    OrderSide,
    Quantity,
    Venue,
)
from nautilus_trader.portfolio import Portfolio
from nautilus_trader.trading import Strategy

from equity_strategy.risk_management.base import BaseRiskManager

logger = logging.getLogger(__name__)


class Rebalancer:
    """
    Nautilus execution-side rebalancer for portfolio target weights.

    Responsible for:
    - Reading current portfolio equity and position states directly from Nautilus.
    - Sizing target quantities from current equity, target weights, and current market prices.
    - Handling entering/increasing instruments and closing/reducing exiting instruments.
    - Enforcing instrument lot size and precision constraints via Nautilus Instrument metadata.
    - Generating and submitting real Nautilus Market orders (sells first, then buys).
    """

    def __init__(
        self,
        strategy: Optional[Strategy] = None,
        *,
        portfolio: Optional[Portfolio] = None,
        cache: Optional[Cache] = None,
        order_factory: Optional[OrderFactory] = None,
        submit_order: Optional[Callable[[MarketOrder], None]] = None,
        default_venue: Optional[Union[Venue, str]] = None,
        base_currency: Optional[Union[Currency, str]] = None,
        risk_manager: Optional[BaseRiskManager] = None,
    ):
        self.strategy = strategy
        self.portfolio: Optional[Portfolio] = (
            portfolio if portfolio is not None else (strategy.portfolio if strategy else None)
        )
        self.cache: Optional[Cache] = (
            cache if cache is not None else (strategy.cache if strategy else None)
        )
        self.order_factory: Optional[OrderFactory] = (
            order_factory if order_factory is not None else (strategy.order_factory if strategy else None)
        )
        self._submit_order_fn: Optional[Callable[[MarketOrder], None]] = (
            submit_order if submit_order is not None else (strategy.submit_order if strategy else None)
        )

        self.default_venue: Optional[Venue] = (
            Venue(default_venue) if isinstance(default_venue, str) else default_venue
        )
        self.base_currency: Optional[Currency] = (
            Currency.from_str(base_currency) if isinstance(base_currency, str) else base_currency
        )
        self.risk_manager: Optional[BaseRiskManager] = risk_manager

        self._auto_infer_venue_and_currency()

    def _auto_infer_venue_and_currency(self) -> None:
        """Attempts to infer default_venue and base_currency from cache if not provided."""
        if self.default_venue is None and self.cache is not None:
            accounts = self.cache.accounts()
            if accounts:
                self.default_venue = accounts[0].id.venue
            else:
                instruments = self.cache.instruments()
                if instruments:
                    self.default_venue = instruments[0].id.venue

        if self.base_currency is None and self.default_venue is not None and self.portfolio is not None:
            acc = self.portfolio.account(venue=self.default_venue)
            if acc is not None:
                currencies = acc.currencies()
                if currencies:
                    self.base_currency = currencies[0]

    def get_current_equity(self) -> float:
        """
        Retrieves the current total portfolio equity from Nautilus.
        Nautilus is the authoritative source of truth for account and portfolio state.
        """
        if self.portfolio is None:
            raise RuntimeError("Rebalancer requires a Nautilus Portfolio instance to query equity.")

        venue = self.default_venue
        eq_dict = self.portfolio.equity(venue=venue)
        if eq_dict:
            if self.base_currency and self.base_currency in eq_dict:
                return float(eq_dict[self.base_currency].as_double())
            return float(list(eq_dict.values())[0].as_double())

        # Fallback to account balance if mark-to-market equity dict is empty
        acc = self.portfolio.account(venue=venue)
        if acc is not None:
            if self.base_currency:
                bal = acc.balance_total(self.base_currency)
                if bal is not None:
                    return float(bal.as_double())
            balances = acc.balances_total()
            if balances:
                return float(list(balances.values())[0].as_double())

        raise ValueError("Unable to determine current portfolio equity from Nautilus.")

    def _resolve_instrument_id(self, key: Union[InstrumentId, str]) -> InstrumentId:
        """Resolves an InstrumentId from an InstrumentId or string representation."""
        if isinstance(key, InstrumentId):
            return key
        key_str = str(key).strip().upper()
        if "." in key_str:
            return InstrumentId.from_str(key_str)
        if self.default_venue is not None:
            return InstrumentId.from_str(f"{key_str}.{self.default_venue}")
        if self.cache is not None:
            for inst in self.cache.instruments():
                if str(inst.id.symbol).upper() == key_str or (
                    hasattr(inst, "raw_symbol") and inst.raw_symbol.upper() == key_str
                ):
                    return inst.id
        raise ValueError(
            f"Cannot resolve InstrumentId for symbol: '{key}'. Please specify default_venue or register instrument."
        )

    def _get_instrument(
        self,
        instrument_id: InstrumentId,
        custom_map: Optional[Dict[InstrumentId, Any]] = None,
    ) -> Any:
        """Retrieves Instrument metadata needed for valid quantities."""
        if custom_map and instrument_id in custom_map:
            return custom_map[instrument_id]
        if self.cache is not None:
            inst = self.cache.instrument(instrument_id)
            if inst is not None:
                return inst
        raise ValueError(f"Instrument metadata not found in Nautilus cache for '{instrument_id}'")

    def _calculate_valid_quantity(self, instrument: Any, raw_qty: float) -> Optional[Quantity]:
        """
        Rounds raw_qty according to Nautilus instrument precision and lot-size constraints.
        Returns a valid Quantity, or None if the quantity rounds to zero or is below min_quantity.
        """
        if raw_qty <= 0:
            return None

        # Check if below min_quantity before rounding
        if getattr(instrument, "min_quantity", None) is not None:
            if raw_qty < float(instrument.min_quantity):
                return None

        try:
            qty = instrument.make_qty(raw_qty)
        except ValueError:
            # Raised by Nautilus if value rounds to zero for the instrument's size precision
            return None

        if qty.as_double() <= 0:
            return None

        if getattr(instrument, "min_quantity", None) is not None:
            if qty < instrument.min_quantity:
                return None

        if getattr(instrument, "max_quantity", None) is not None:
            if qty > instrument.max_quantity:
                qty = instrument.max_quantity

        return qty

    def rebalance(
        self,
        target_weights: Dict[Union[InstrumentId, str], float],
        current_prices: Dict[Union[InstrumentId, str], float],
        *,
        instruments: Optional[Dict[Union[InstrumentId, str], Any]] = None,
    ) -> List[MarketOrder]:
        """
        Executes a portfolio rebalance to achieve the specified target weights.

        Parameters:
            target_weights: Mapping from InstrumentId/symbol to target portfolio weight (e.g. 0.05).
            current_prices: Mapping from InstrumentId/symbol to current market price.
            instruments: Optional mapping from InstrumentId/symbol to Instrument metadata.

        Returns:
            List[MarketOrder]: Chronological list of submitted market orders (SELLs first, then BUYs).
        """
        if self.portfolio is None or self.order_factory is None or self._submit_order_fn is None:
            raise RuntimeError(
                "Rebalancer cannot execute: portfolio, order_factory, and submit_order must be initialized."
            )

        # 1. Validate target weights
        total_weight = 0.0
        normalized_weights: Dict[InstrumentId, float] = {}
        for key, w in target_weights.items():
            if not isinstance(w, (int, float)) or pd.isna(w):
                raise ValueError(f"Invalid target weight for '{key}': {w}")
            weight = float(w)
            if weight < 0.0:
                raise ValueError(f"Negative target weight not allowed for '{key}': {weight}")
            total_weight += weight
            inst_id = self._resolve_instrument_id(key)
            normalized_weights[inst_id] = weight

        if total_weight > 1.0001:
            raise ValueError(f"Sum of target weights ({total_weight:.4f}) exceeds 100%.")

        # 2. Retrieve current equity and calculate deployment budget
        current_equity = self.get_current_equity()
        if current_equity <= 0.0:
            raise ValueError(f"Portfolio equity must be positive to size positions, got {current_equity:.2f}")

        deployment_budget = self.risk_manager.calculate_deployment_budget(current_equity)

        logger.info(
            "Starting rebalance: Portfolio Equity=%.2f, Deployment Budget=%.2f, Target Holdings=%d, Total Weight=%.4f",
            current_equity,
            deployment_budget,
            len(normalized_weights),
            total_weight,
        )

        # 3. Handle instruments missing from target weights
        # Any currently-held instrument missing from target_weights must have target_weight = 0
        if self.cache is not None:
            for pos in self.cache.positions_open():
                inst_id = pos.instrument_id
                net_qty = float(self.portfolio.net_position(inst_id))
                if net_qty != 0.0 and inst_id not in normalized_weights:
                    logger.info("Held instrument %s missing from target weights; treating as target_weight=0.0", inst_id)
                    normalized_weights[inst_id] = 0.0

        # 4. Normalize prices and custom instruments
        normalized_prices: Dict[InstrumentId, float] = {}
        for key, p in current_prices.items():
            inst_id = self._resolve_instrument_id(key)
            if not isinstance(p, (int, float)) or pd.isna(p) or p <= 0.0:
                raise ValueError(f"Invalid market price for '{inst_id}': {p}")
            normalized_prices[inst_id] = float(p)

        inst_map: Dict[InstrumentId, Any] = {}
        if instruments:
            for key, inst in instruments.items():
                inst_id = self._resolve_instrument_id(key)
                inst_map[inst_id] = inst

        # 5. Check price availability for all target holdings with weight > 0
        for inst_id, weight in normalized_weights.items():
            if weight > 0.0 and inst_id not in normalized_prices:
                raise ValueError(f"Missing current market price for target instrument '{inst_id}'")

        # 6. Sizing and separating orders into SELLs and BUYs
        sell_orders: List[Tuple[MarketOrder, InstrumentId, float, float, float, float, float]] = []
        buy_orders: List[Tuple[MarketOrder, InstrumentId, float, float, float, float, float]] = []

        for inst_id, weight in normalized_weights.items():
            instrument = self._get_instrument(inst_id, custom_map=inst_map)
            current_qty = float(self.portfolio.net_position(inst_id))
            target_value = deployment_budget * weight
            price = normalized_prices.get(inst_id, 0.0)

            target_qty = (target_value / price) if price > 0.0 else 0.0
            required_qty = target_qty - current_qty

            if required_qty < 0:
                # Need to SELL
                raw_sell_qty = abs(current_qty) if weight == 0.0 else abs(required_qty)
                order_qty = self._calculate_valid_quantity(instrument, raw_sell_qty)

                if order_qty is None or order_qty.as_double() == 0:
                    logger.debug("Delta for %s is negligible; no sell order generated", inst_id)
                    continue

                order = self.order_factory.market(
                    instrument_id=inst_id,
                    order_side=OrderSide.SELL,
                    quantity=order_qty,
                )
                sell_orders.append((order, inst_id, current_qty, target_qty, required_qty, price, weight))

            elif required_qty > 0:
                # Need to BUY
                raw_buy_qty = required_qty
                order_qty = self._calculate_valid_quantity(instrument, raw_buy_qty)

                if order_qty is None or order_qty.as_double() == 0:
                    logger.debug("Delta for %s is negligible; no buy order generated", inst_id)
                    continue

                order = self.order_factory.market(
                    instrument_id=inst_id,
                    order_side=OrderSide.BUY,
                    quantity=order_qty,
                )
                buy_orders.append((order, inst_id, current_qty, target_qty, required_qty, price, weight))

        # 7. Rebalance Sequencing: Submit SELLs first to free capital, then submit BUYs
        submitted_orders: List[MarketOrder] = []

        for order, inst_id, cur_q, tgt_q, delta_q, px, w in sell_orders:
            logger.info(
                "REBALANCE ORDER [SELL]: %s | order_qty=%s | current_qty=%.2f -> target_qty=%.2f (delta=%.2f) | price=%.2f | weight=%.4f",
                inst_id,
                order.quantity,
                cur_q,
                tgt_q,
                delta_q,
                px,
                w,
            )
            self._submit_order_fn(order)
            submitted_orders.append(order)

        for order, inst_id, cur_q, tgt_q, delta_q, px, w in buy_orders:
            logger.info(
                "REBALANCE ORDER [BUY]:  %s | order_qty=%s | current_qty=%.2f -> target_qty=%.2f (delta=%.2f) | price=%.2f | weight=%.4f",
                inst_id,
                order.quantity,
                cur_q,
                tgt_q,
                delta_q,
                px,
                w,
            )
            self._submit_order_fn(order)
            submitted_orders.append(order)

        logger.info(
            "Rebalance complete: Submitted %d orders (%d SELLs, %d BUYs)",
            len(submitted_orders),
            len(sell_orders),
            len(buy_orders),
        )
        return submitted_orders
