import argparse
import logging
import pathlib
import yaml
import pandas as pd

from equity_strategy.data.loader import CSVDataLoader
from equity_strategy.research.target_generator import TargetWeightGenerator
from equity_strategy.nautilus.backtest import NautilusBacktestRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Run NautilusTrader 2.0 Backtest")
    parser.add_argument("--data-dir", type=str, default="data/sample", help="Directory containing input market data CSVs")
    parser.add_argument("--targets-file", type=str, default="data/outputs/target_weights.parquet", help="Path to target weights parquet file")
    parser.add_argument("--config", type=str, default="config/strategy_config.yaml", help="Path to strategy config YAML")
    parser.add_argument("--output-dir", type=str, default="data/outputs", help="Directory to save performance results")
    args = parser.parse_args()

    # 1. Load Config
    config_path = pathlib.Path(args.config)
    config = {}
    if config_path.exists():
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}

    # 2. Load Raw Market Data
    logger.info(f"Loading market data from '{args.data_dir}'...")
    loader = CSVDataLoader(args.data_dir)
    df_raw = loader.load_data(validate=True)

    # 3. Load or Generate Target Weights
    targets_path = pathlib.Path(args.targets_file)
    if targets_path.exists():
        logger.info(f"Loading target weights from '{targets_path}'...")
        df_targets = pd.read_parquet(targets_path)
    else:
        logger.info(f"Target weights file '{targets_path}' not found. Generating target weights...")
        generator = TargetWeightGenerator(config=config)
        df_targets = generator.generate(df_raw)
        generator.save_targets(df_targets, output_dir=args.output_dir)

    # 4. Run Nautilus Backtest
    runner = NautilusBacktestRunner(config=config)
    metrics, equity_curve = runner.run(df_raw, df_targets)

    logger.info("============================================================")
    logger.info("NAUTILUSTRADER BACKTEST PERFORMANCE RESULTS")
    logger.info("============================================================")
    logger.info(f"Initial Capital   : INR {metrics['initial_capital']:,.2f}")
    logger.info(f"Final Value       : INR {metrics['final_value']:,.2f}")
    logger.info(f"Total Return      : {metrics['total_return']*100:.2f}%")
    logger.info(f"Annualized Return : {metrics['annualized_return']*100:.2f}%")
    logger.info(f"Annualized Vol    : {metrics['annualized_volatility']*100:.2f}%")
    logger.info(f"Sharpe Ratio      : {metrics['sharpe_ratio']:.2f}")
    logger.info(f"Max Drawdown      : {metrics['max_drawdown']*100:.2f}%")
    logger.info(f"Output Directory  : {args.output_dir}")
    logger.info("============================================================")

if __name__ == "__main__":
    main()
