"""
qbeast_backtest/nautilus_backtest.py
---------------------------------
NautilusTrader migration — STAGE 1: the execution foundation (venue + INR instruments + fee/fill wiring).

Investigation result (why this file is structured the way it is):
  * Nautilus HAS native charges + fills — FeeModel / FillModel, per-position commissions & realized PnL.
  * Nautilus has NO capital-gains tax and NO idle-cash yield — zero references in the package. Those two
    (Indian FIFO STCG/LTCG + Rs 1.25L exemption + FY accrual, and the 4.5% cash yield) must be CUSTOM layers
    bolted on top (stage 4), reading Nautilus fills/positions.

Faithful mapping of the current engine.py onto Nautilus:
  charges (COST_PCT + per-stock liquidity slippage) -> a CUSTOM FeeModel (Nautilus's FillModel slippage is
      probabilistic, not a deterministic per-stock bps, so a custom FeeModel reproduces engine.py exactly).
  "buy on available cash" (no leverage)             -> add_venue(allow_cash_borrowing=False)  [done here].
  next-day-open fills                               -> bar_execution + submit on the signal bar, fill next bar.
  capital-gains tax + cash yield                    -> STAGE 4 custom layer (Nautilus has neither).

Staged plan:
  STAGE 1 (this) : venue + INR equities + fee/fill wiring          [runnable, verified below]
  STAGE 2        : load daily bars for the full universe -> engine
  STAGE 3        : the Nautilus Strategy that executes stage 2's targets (monthly rebalance orders)
  STAGE 4        : custom TAX + CASH-YIELD post-layer (the two things Nautilus lacks)
  STAGE 5        : reconcile the Nautilus result against engine.py (must match CAGR 12.20% / maxDD -28.2%)

Run:  python nautilus_backtest.py        (stage 1: builds + reports the execution venue)
"""
from __future__ import annotations

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FeeModel, FillModel, FixedFeeModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Money, Price, Quantity

from qbeast_backtest.config import COST_PCT

from qbeast_exec.nautilus import make_equity as _qe_make_equity

VENUE = Venue("XNSE")


def RegimeFeeModel(cost_pct: float | None = None, slippage: dict | None = None):
    """The Nautilus fee model, built from the SHARED charge schedule (config.charges_config()).

    This was a local FeeModel multiplying notional by COST_PCT — a third copy of "what a trade costs",
    alongside engine.py's and qbeast_exec's. It now returns qbeast_exec.NautilusFeeModel, so the
    Nautilus path and the internal ledger are charged by the same code. In the default "blended" mode
    that is COST_PCT x notional plus the per-stock slippage rate, i.e. arithmetically what it was.

    `cost_pct` is accepted for call-site compatibility and IGNORED unless it differs from config.COST_PCT
    (in which case it wins, for a deliberate one-off sensitivity run)."""
    from qbeast_backtest.config import COST_PCT as _CFG_COST, CHARGES_MODE, charges_config
    from qbeast_exec import ChargesConfig, NautilusFeeModel

    cfg = charges_config()
    if cost_pct is not None and CHARGES_MODE == "blended" and float(cost_pct) != float(_CFG_COST):
        cfg = ChargesConfig.blended(rate=float(cost_pct))     # explicit override for a sensitivity run
    return NautilusFeeModel(cfg, extra_rate=dict(slippage or {}))


def index_instrument_symbol() -> str:
    """The Nautilus instrument symbol for the benchmark, derived from config.INDEX_SYMBOL.

    It used to be the literal "NIFTY100" in every Nautilus entry point, so switching UNIVERSE left the
    rebalance clock named after the wrong index. Nautilus symbols cannot contain spaces, so "NIFTY 100"
    becomes "NIFTY100" here — a formatting rule, not a hard-coded universe."""
    from qbeast_backtest.config import INDEX_SYMBOL
    return "".join(str(INDEX_SYMBOL).split()).upper()


def make_equity(symbol: str) -> Equity:
    """An NSE delivery equity: INR-quoted, 0.05 tick, single-share lots (no derivatives multiplier).

    DELEGATES to `qbeast_exec.nautilus.make_equity`, with the VENUE PASSED EXPLICITLY.

    That last part matters. The two implementations did NOT agree: this module has used
    `Venue("XNSE")` since the migration, while qbeast_exec defaults to `"NSE"`. Delegating without
    passing it would have renamed every instrument id in every order book and fill export - silently,
    and only visibly in files nobody diffs. XNSE is the venue the reconciliation validates, so it is
    the one that stays, and it is named here rather than relying on either default.
    """
    return _qe_make_equity(symbol, venue=VENUE.value, currency=INR.code,
                           tick="0.05", precision=2)


def build_engine(starting_inr: int = 1_000_000, fee_model: FeeModel | None = None,
                 fill_model: FillModel | None = None) -> BacktestEngine:
    """A cash INR venue with NO cash borrowing (== buy only on available cash) and next-bar execution.
    Pass a RegimeFeeModel for faithful COST_PCT + per-stock slippage; defaults to a tiny placeholder.
    Pass a custom `fill_model` (e.g. DynamicSlippageFillModel) to move the FILL PRICE stochastically;
    defaults to the deterministic no-slippage fill (prob_slippage=0.0) used for the parity baseline."""
    cfg = BacktestEngineConfig(trader_id="REGIME-001", logging=LoggingConfig(log_level="ERROR"))
    engine = BacktestEngine(config=cfg)
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=INR,
        starting_balances=[Money(starting_inr, INR)],
        fill_model=fill_model if fill_model is not None
        else FillModel(prob_fill_on_limit=1.0, prob_slippage=0.0, random_seed=7),
        fee_model=fee_model if fee_model is not None else FixedFeeModel(Money(1, INR)),
        allow_cash_borrowing=False,                # "buy on available cash" — no leverage, no negative cash
        bar_execution=True,
    )
    return engine


def main() -> None:
    engine = build_engine()
    for sym in ("RELIANCE", "TCS", "INFY"):
        engine.add_instrument(make_equity(sym))
    print("STAGE 1 OK — Nautilus execution foundation is live.")
    print(f"  trader        : {engine.trader_id}")
    print(f"  venue         : {VENUE}  (CASH / INR, allow_cash_borrowing=False -> buy on available cash)")
    print(f"  execution     : bar_execution=True (submit on signal bar -> fills next bar = next-day open)")
    print(f"  instruments   : {[str(i.id) for i in engine.cache.instruments()]}")
    print(f"  charges/slip  : native FeeModel/FillModel present; STAGE 2 swaps in a custom FeeModel to match")
    print(f"                  engine.py's COST_PCT + per-stock liquidity slippage exactly.")
    print(f"  tax + yield   : NOT in Nautilus -> STAGE 4 custom layer (FIFO STCG/LTCG + Rs1.25L + 4.5% cash).")
    engine.dispose()


if __name__ == "__main__":
    main()
