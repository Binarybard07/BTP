"""
Indian Equity Tax Accounting package.
"""
from equity_strategy.tax.accounting import (
    LTCG_EXEMPTION,
    LTCG_RATE,
    STCG_RATE,
    AnnualTaxRecord,
)
from equity_strategy.tax.lot import BuyLot, FIFOMatcher, RealisedTrade
from equity_strategy.tax.tax_module import IndianEquityTaxModule

__all__ = [
    "BuyLot",
    "RealisedTrade",
    "FIFOMatcher",
    "AnnualTaxRecord",
    "IndianEquityTaxModule",
    "STCG_RATE",
    "LTCG_RATE",
    "LTCG_EXEMPTION",
]
