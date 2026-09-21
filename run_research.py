import argparse
import logging
import pathlib
import yaml
import pandas as pd

from equity_strategy.data.loader import CSVDataLoader
from equity_strategy.research.target_generator import TargetWeightGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Run V1 Indian Equity Target Weight Research Pipeline")
    parser.add_argument("--data-dir", type=str, default="data/sample", help="Directory containing input market data CSVs")
    parser.add_argument("--config", type=str, default="config/strategy_config.yaml", help="Path to strategy config YAML")
    parser.add_argument("--output-dir", type=str, default="data/outputs", help="Directory to output target weight files")
    args = parser.parse_args()

    # 1. Load Configuration
    config_path = pathlib.Path(args.config)
    config = {}
    if config_path.exists():
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}

    logger.info(f"Loading raw market data from directory: '{args.data_dir}'...")

    # 2. Load & Validate Data
    loader = CSVDataLoader(args.data_dir)
    df_raw = loader.load_data(validate=True)
    logger.info(f"Loaded {len(df_raw)} validated rows for {df_raw['Symbol'].nunique()} unique symbols.")

    # 3. Generate Target Weights
    generator = TargetWeightGenerator(config=config)
    df_targets = generator.generate(df_raw)

    # 4. Save Output Files
    generator.save_targets(df_targets, output_dir=args.output_dir)

    num_sessions = df_targets["EffectiveDate"].nunique() if not df_targets.empty else 0
    total_target_records = len(df_targets)

    logger.info("============================================================")
    logger.info("RESEARCH PIPELINE EXECUTION COMPLETE")
    logger.info("============================================================")
    logger.info(f"Rebalance Sessions  : {num_sessions}")
    logger.info(f"Total Target Alloc  : {total_target_records}")
    logger.info(f"Target Weights File : {args.output_dir}/target_weights.parquet")
    logger.info("============================================================")

if __name__ == "__main__":
    main()
