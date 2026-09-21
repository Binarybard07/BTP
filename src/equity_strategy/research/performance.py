import pathlib
import logging
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)

class PerformanceAnalyzer:
    """
    Computes portfolio analytics, Sharpe ratio, drawdown, equity curves, and performance summaries.
    """

    def __init__(self, starting_capital: float = 1_000_000.0):
        self.starting_capital = starting_capital

    def analyze(
        self,
        equity_curve: pd.DataFrame,
        trades_df: Optional[pd.DataFrame] = None
    ) -> Dict[str, Any]:
        """
        Calculates performance summary metrics from an equity curve DataFrame.
        equity_curve DataFrame must contain ['Date', 'Equity'].
        """
        if equity_curve.empty or len(equity_curve) < 2:
            return {
                "initial_capital": self.starting_capital,
                "final_value": self.starting_capital,
                "total_return": 0.0,
                "annualized_return": 0.0,
                "annualized_volatility": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "total_trades": len(trades_df) if trades_df is not None else 0,
                "turnover": 0.0,
            }

        df = equity_curve.copy()
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values(by="Date").reset_index(drop=True)

        start_val = df["Equity"].iloc[0]
        final_val = df["Equity"].iloc[-1]
        total_ret = (final_val - start_val) / start_val

        # Daily returns
        df["DailyReturn"] = df["Equity"].pct_change().fillna(0.0)

        n_days = (df["Date"].iloc[-1] - df["Date"].iloc[0]).days
        n_trading_days = len(df)

        if n_days > 0 and n_trading_days > 1:
            ann_factor = 252.0 / n_trading_days
            ann_return = (1.0 + total_ret) ** ann_factor - 1.0
        else:
            ann_return = 0.0

        daily_std = df["DailyReturn"].std()
        ann_vol = daily_std * np.sqrt(252.0) if not np.isnan(daily_std) else 0.0

        sharpe = ann_return / ann_vol if ann_vol > 0 else 0.0

        # Maximum Drawdown calculation
        df["Peak"] = df["Equity"].cummax()
        df["Drawdown"] = (df["Equity"] - df["Peak"]) / df["Peak"]
        max_drawdown = abs(df["Drawdown"].min()) if not df["Drawdown"].empty else 0.0

        total_trades = len(trades_df) if trades_df is not None else 0

        # Calculate approximate turnover if trades are provided
        turnover = 0.0
        if trades_df is not None and not trades_df.empty and "Value" in trades_df.columns:
            total_traded_value = trades_df["Value"].abs().sum()
            avg_portfolio_val = df["Equity"].mean()
            turnover = total_traded_value / avg_portfolio_val if avg_portfolio_val > 0 else 0.0

        metrics = {
            "initial_capital": start_val,
            "final_value": final_val,
            "total_return": total_ret,
            "annualized_return": ann_return,
            "annualized_volatility": ann_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_drawdown,
            "total_trades": total_trades,
            "turnover": turnover,
        }
        return metrics

    def save_reports(
        self,
        metrics: Dict[str, Any],
        equity_curve: pd.DataFrame,
        output_dir: str = "data/outputs"
    ):
        """Saves performance summary CSV, equity curve CSV, and equity curve plot PNG."""
        out_path = pathlib.Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # 1. Performance Summary CSV
        summary_df = pd.DataFrame([metrics])
        summary_path = out_path / "performance_summary.csv"
        summary_df.to_csv(summary_path, index=False)

        # 2. Equity Curve CSV
        equity_path = out_path / "equity_curve.csv"
        equity_curve.to_csv(equity_path, index=False)

        # 3. Equity Curve PNG
        try:
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.plot(pd.to_datetime(equity_curve["Date"]), equity_curve["Equity"], label="Strategy Equity", color="#1f77b4")
            ax.set_title("V1 Equity Strategy Performance")
            ax.set_xlabel("Date")
            ax.set_ylabel("Portfolio Value (INR)")
            ax.grid(True, linestyle="--", alpha=0.5)
            ax.legend()
            fig.tight_layout()

            img_path = out_path / "equity_curve.png"
            fig.savefig(img_path, dpi=150)
            plt.close(fig)
            logger.info(f"Performance reports saved to '{out_path}'.")
        except Exception as e:
            logger.warning(f"Could not render equity curve plot: {e}")
