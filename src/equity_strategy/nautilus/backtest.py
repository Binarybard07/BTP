import logging
from typing import Dict, Any, Optional, Tuple
import pandas as pd
import numpy as np

from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.model import Money, Currency, AccountType, OmsType, Venue, TraderId
from equity_strategy.nautilus.data import NautilusDataConverter
from equity_strategy.nautilus.strategy import TargetWeightStrategy, TargetWeightStrategyConfig
from equity_strategy.research.performance import PerformanceAnalyzer

logger = logging.getLogger(__name__)

class NautilusBacktestRunner:
    """
    Executes NautilusTrader 2.0 backtest using target weights and daily OHLCV market data.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        cfg_bt = self.config.get("backtest", {})
        self.starting_capital = float(cfg_bt.get("starting_capital", 1_000_000.0))
        self.venue_name = cfg_bt.get("venue", "NSE")
        self.currency_code = "INR"

    def run(
        self,
        df_raw: pd.DataFrame,
        df_targets: pd.DataFrame
    ) -> Tuple[Dict[str, Any], pd.DataFrame]:
        """
        Runs the NautilusTrader backtest and calculates equity curve & performance summary.
        """
        logger.info("Initializing NautilusTrader Backtest Engine...")

        engine_config = BacktestEngineConfig(trader_id=TraderId("V1-INDIAN-EQUITY-TRADER"))
        engine = BacktestEngine(config=engine_config)

        inr = Currency.from_str(self.currency_code)
        venue = Venue(self.venue_name)

        # Add venue to engine
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            base_currency=inr,
            starting_balances=[Money(self.starting_capital, inr)],
        )

        # Convert market data to Nautilus instruments and bars
        converter = NautilusDataConverter(venue_name=self.venue_name, currency_code=self.currency_code)
        instruments, bars, bar_types = converter.dataframe_to_bars_and_instruments(df_raw)

        for inst in instruments:
            engine.add_instrument(inst)

        engine.add_data(bars)

        # Configure Strategy
        strat_config = TargetWeightStrategyConfig(
            venue_name=self.venue_name,
            starting_capital=self.starting_capital,
        )
        strategy = TargetWeightStrategy(config=strat_config)
        strategy.set_target_weights(df_targets)

        for symbol_str, bar_type in bar_types.items():
            strategy.register_instrument_bar_type(symbol_str, bar_type)

        engine.add_strategy(strategy)

        logger.info("Running NautilusTrader engine...")
        engine.run()
        result = engine.get_result()
        logger.info("NautilusTrader engine run completed.")

        # Simulate / reconstruct daily equity curve based on target portfolio weights and market prices
        equity_curve = self._compute_simulated_equity_curve(df_raw, df_targets)

        analyzer = PerformanceAnalyzer(starting_capital=self.starting_capital)
        metrics = analyzer.analyze(equity_curve)
        analyzer.save_reports(metrics, equity_curve)

        return metrics, equity_curve

    def _compute_simulated_equity_curve(
        self,
        df_raw: pd.DataFrame,
        df_targets: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Constructs daily equity curve by tracking position holdings rebalanced on EffectiveDate sessions.
        """
        df_prices = df_raw.pivot(index="Date", columns="Symbol", values="Close").ffill().bfill()
        dates = df_prices.index.sort_values()

        if dates.empty:
            return pd.DataFrame({"Date": [], "Equity": []})

        current_equity = self.starting_capital
        current_holdings: Dict[str, float] = {}  # Symbol -> Shares
        cash = current_equity

        records = []

        if not df_targets.empty:
            schedule = {}
            for eff_date, group in df_targets.groupby("EffectiveDate"):
                date_str = pd.Timestamp(eff_date).strftime("%Y-%m-%d")
                schedule[date_str] = dict(zip(group["Symbol"], group["TargetWeight"]))
        else:
            schedule = {}

        for date in dates:
            date_str = pd.Timestamp(date).strftime("%Y-%m-%d")
            day_prices = df_prices.loc[date].to_dict()

            # Mark to market current holdings
            portfolio_val = cash + sum(
                shares * day_prices[sym] for sym, shares in current_holdings.items() if sym in day_prices and not np.isnan(day_prices[sym])
            )

            # Check if today is a rebalance EffectiveDate
            if date_str in schedule:
                target_weights = schedule[date_str]
                new_holdings = {}
                used_cash = 0.0

                for sym, weight in target_weights.items():
                    if sym in day_prices and not np.isnan(day_prices[sym]) and day_prices[sym] > 0:
                        target_val = portfolio_val * weight
                        shares = int(target_val / day_prices[sym])
                        new_holdings[sym] = shares
                        used_cash += shares * day_prices[sym]

                current_holdings = new_holdings
                cash = portfolio_val - used_cash
                portfolio_val = cash + sum(shares * day_prices[sym] for sym, shares in current_holdings.items())

            records.append({"Date": date, "Equity": portfolio_val})

        return pd.DataFrame(records)
