"""
Indian equity capital gains loss netting and tax accounting.
"""
from dataclasses import dataclass
from typing import Dict

# Fixed conservative tax assumptions (simplified for simulation)
STCG_RATE: float = 0.20
LTCG_RATE: float = 0.125
LTCG_EXEMPTION: float = 100_000.0


@dataclass
class AnnualTaxRecord:
    """
    Maintains and nets capital gains and losses for one Indian Financial Year (ending 31 March).
    """
    fy_year: int
    gross_stcg: float = 0.0
    gross_stcl: float = 0.0
    gross_ltcg: float = 0.0
    gross_ltcl: float = 0.0

    def add_realised_trade(self, is_long_term: bool, pnl: float) -> None:
        """Records a realised trade PnL into the appropriate STCG/STCL/LTCG/LTCL bucket."""
        if is_long_term:
            if pnl >= 0.0:
                self.gross_ltcg += pnl
            else:
                self.gross_ltcl += abs(pnl)
        else:
            if pnl >= 0.0:
                self.gross_stcg += pnl
            else:
                self.gross_stcl += abs(pnl)

    def calculate_tax(self) -> Dict[str, float]:
        """
        Calculates annual tax applying Indian loss set-off hierarchy:
        1. STCL offsets STCG first.
        2. Any remaining STCL offsets LTCG.
        3. LTCL offsets LTCG only.
        4. LTCL must not offset STCG.

        Tax calculation:
        - Taxable STCG = remaining net STCG * 20%.
        - Taxable LTCG = max(0, eligible net LTCG - 100,000).
        - LTCG tax = taxable LTCG * 12.5%.
        - Total tax = STCG tax + LTCG tax.
        """
        # Step 1: STCL offsets STCG first
        if self.gross_stcg >= self.gross_stcl:
            net_stcg = self.gross_stcg - self.gross_stcl
            remaining_stcl = 0.0
        else:
            net_stcg = 0.0
            remaining_stcl = self.gross_stcl - self.gross_stcg

        # Step 2: Any remaining STCL offsets LTCG
        ltcg_after_stcl = max(0.0, self.gross_ltcg - remaining_stcl)

        # Step 3: LTCL offsets LTCG only
        eligible_net_ltcg = max(0.0, ltcg_after_stcl - self.gross_ltcl)

        # Step 4: LTCL must not offset STCG -> net_stcg is untouched by LTCL

        # Tax calculation
        taxable_stcg = net_stcg
        stcg_tax = taxable_stcg * STCG_RATE

        taxable_ltcg = max(0.0, eligible_net_ltcg - LTCG_EXEMPTION)
        ltcg_tax = taxable_ltcg * LTCG_RATE

        total_tax = stcg_tax + ltcg_tax

        return {
            "fy_year": float(self.fy_year),
            "gross_stcg": self.gross_stcg,
            "gross_stcl": self.gross_stcl,
            "gross_ltcg": self.gross_ltcg,
            "gross_ltcl": self.gross_ltcl,
            "net_stcg": net_stcg,
            "eligible_net_ltcg": eligible_net_ltcg,
            "taxable_stcg": taxable_stcg,
            "taxable_ltcg": taxable_ltcg,
            "stcg_tax": stcg_tax,
            "ltcg_tax": ltcg_tax,
            "total_tax": total_tax,
        }


__all__ = [
    "STCG_RATE",
    "LTCG_RATE",
    "LTCG_EXEMPTION",
    "AnnualTaxRecord",
]
