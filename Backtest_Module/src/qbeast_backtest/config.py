"""
THIS FILE IS THIS PACKAGE'S OWN CONFIGURATION, and the only place its settings live.

It began as a copy of Backtest_Core/config.py and is now independent: nothing pulls from that folder,
and `qbeast_backtest` runs with Backtest_Core absent or deleted. The two will drift, which is the
point - this package is meant to be installed and run on its own. `reconcile.py` is the one tool that
still looks for Backtest_Core, and it skips cleanly when it is not there.

qbeast_backtest/config.py
----------------------
Single source of truth for the regime-switch strategy. This is a fresh, standalone project: it reads
CLEAN DATA from the existing source (read-only, no duplication) and implements the regime-adaptive
strategy that won the head-to-head comparison ("Task B") in the research project.

Strategy in one line: momentum selection, but adapt HOW MUCH to deploy to the market regime -
cash below the trend (crash protection), full when the uptrend is calm, vol-target-sized when it is
choppy. See DOCUMENTATION.md for the what / how / why.
"""
from __future__ import annotations

import os as _os
from pathlib import Path

import pandas as pd

# --- INTERPRETER GUARD ----------------------------------------------------------------------------
# The mechanism lives in preflight.py (stdlib-only, no side effects) so that a STANDALONE script can
# guard itself without importing this file and inheriting requirements it does not share. Re-exported
# here because config.py is imported by every entry point in the project: one import, and every script
# picks up the configuration-driven check at the bottom of this file.
#   config.require("plotly")      <- what a particular script needs, asked for by that script
from qbeast_backtest.preflight import require  # noqa: F401  (re-exported for every module that imports config)

# --- DATA: pick the UNIVERSE by name --------------------------------------------------------------
# A universe is FOUR coupled settings (folder, benchmark symbol, membership file, Upstox index key).
# Changing them by hand once wrote MidCap-150 prices into the file named "NIFTY 100", silently corrupting
# the benchmark AND the trend gate. So set ONE name here; universes.py supplies the rest, consistently.
#
#   run  `python universes.py`  to list every preset and validate it against the disk.
#   options: nifty100 | nifty100_clean | midcap150 | smallcap250 | smallcap250_ddq   (add your own there)
# ENV OVERRIDES, applied BEFORE anything is derived from them.
# UNIVERSE cannot be changed after import: DATA_DIR, OUT_DIR, the membership file and the benchmark
# are all derived from it here. So a caller that needs a different universe - a sweep, or
# `qbeast_backtest.reconcile` adopting another checkout's settings - sets these and imports fresh.
#   QBEAST_BACKTEST_UNIVERSE   e.g. "smallcap250"
#   QBEAST_BACKTEST_START      e.g. "2016-04-29"
#   QBEAST_BACKTEST_END        e.g. "2026-05-29"
_ENV_UNIVERSE = _os.environ.get("QBEAST_BACKTEST_UNIVERSE")
_ENV_START = _os.environ.get("QBEAST_BACKTEST_START")
_ENV_END = _os.environ.get("QBEAST_BACKTEST_END")

from qbeast_backtest import universes as _U

UNIVERSE = _ENV_UNIVERSE or "midcap150_ddq"

_u = _U.get(UNIVERSE)
_OV = _U.overrides(UNIVERSE)      # per-universe overrides; an unknown key raises rather than sitting dead


def _ov(key: str, default):
    """Resolve one setting: this universe's override if its preset carries one, else the default below.

    Every tunable in this file goes through here, so a setting that must differ by universe (small-cap
    slippage, a smaller book, a different execution engine) is changed once in universes.py - not by
    editing this file and forgetting to change it back. `python universes.py` prints what each overrides."""
    return _OV.get(key, default)


SOURCE = Path(_u["source"])
DATA_DIR = SOURCE                               # per-symbol OHLCV CSVs + the index file
INDEX_SYMBOL = _u["index_symbol"]   # BENCHMARK: MUST match the index CSV's filename in SOURCE

# --- SIGNAL index: which index the REGIME / TREND GATE reads ---------------------------------------
# INDEX_SYMBOL does two jobs by default: it is the BENCHMARK (what returns are reported against, and the
# trading calendar) AND the series the trend gate / vol regime run on. Those are separable: you may want
# to TRADE the NIFTY-100 universe but read the REGIME off NIFTY 50 (broader, longer history, less noisy).
#   SIGNAL_INDEX = None      -> use the benchmark (default; identical to previous behaviour)
#   SIGNAL_INDEX = "Nifty 50"-> gate on NIFTY 50 while still trading + benchmarking this universe
# The file must exist as <SIGNAL_INDEX>.csv inside SIGNAL_INDEX_DIR.
INDICES_DIR = _U.DATA_ROOT / "Final_Indices_Data"   # shared folder of index series (NIFTY 50/100/200/...), under QBEAST_DATA_ROOT
SIGNAL_INDEX = _ov("signal_index", None)
SIGNAL_INDEX_DIR = Path(_ov("signal_index_dir", INDICES_DIR))

USE_MEMBERSHIP = _u["membership"] is not None   # True -> restrict picks to POINT-IN-TIME index members;
                                                # False -> the universe is EVERY *.csv in DATA_DIR (bar the index)
# Kept as a Path even when unused: membership.py does Path(MEMBERSHIP_FILE).name unconditionally, and a
# None here would raise before the USE_MEMBERSHIP check is ever reached.
MEMBERSHIP_FILE = Path(_u["membership"]) if _u["membership"] else SOURCE / "_no_membership_file.csv"
# --- manual override (uncomment to bypass the preset for a one-off experiment) ---
# SOURCE = Path(r"E:\some\other\folder"); DATA_DIR = SOURCE
# INDEX_SYMBOL = "NIFTY 100"; MEMBERSHIP_FILE = None; USE_MEMBERSHIP = False

DATE_FORMAT = "%d-%m-%Y"                        # every file stores dates as DD-MM-YYYY

# --- backtest window ------------------------------------------------------------------------------
# Signals use the FULL price history (so momentum has its 252-day warmup); we TRADE from START.
BACKTEST_START = pd.Timestamp(_ENV_START) if _ENV_START else pd.Timestamp("2016-09-30")   # set by single_file_multiple_universe_run.py
                                              # file. Starting earlier reuses this list retroactively (a
                                              # forward-looking survivorship bias) -- see membership.py.
BACKTEST_END = pd.Timestamp(_ENV_END) if _ENV_END else pd.Timestamp("2026-05-29")            # backtest (research) runs THROUGH this date ...
PAPER_START = BACKTEST_END + pd.Timedelta(days=1)    # ... and the forward PAPER trade begins the next day
                                                     # (01-06-2026 here). Auto-aligns if BACKTEST_END changes.

# --- strategy parameters ---------------------------------------------------------------------------
# THE REMOVED STRATEGY'S TUNED PARAMETERS WERE HERE - its signal, gate and sizing settings, each
# one a choice made by fitting.
#
# They went with the strategy. Nothing in this build reads them, and shipping a strategy's tuning
# without its code is the worst of both: useless to whoever receives it, and a disclosure to anyone
# who recognises what the numbers are.
#
# TWO SETTINGS FROM THAT BLOCK ARE NOT TUNING, AND STAY:
TOP_N = _ov("top_n", 20)       # how many names the book holds. Universe-wide, not strategy-specific:
                               # stage 1's staircase log reports against it, the capital-adequacy
                               # warning sizes a slot from it, and every strategy reads it.
CASH_BUFFER = 0.97             # the deployment CEILING. `StrategyOutput.validate()` refuses weights
                               # summing above it as unauthorised leverage, so this is an enforced
                               # framework rule - removing it would not relax the rule, it would
                               # break the check. ~3% also funds costs and overnight gaps.

# A STRATEGY DECLARES ITS OWN PARAMETERS. Read them from config.py if they are universe-wide (TOP_N
# and REBALANCE_FREQ below are, and the execution layer uses them too), or keep them in your strategy
# class and report them in `StrategyOutput(params=...)` - which is what the dashboard prints and what
# summary.json records, so a result always carries the settings that produced it.

# --- CROSS-SECTIONAL WEIGHTING inside the deployed sleeve (portfolio.py) ---------------------------
# The gate decides HOW MUCH to deploy and momentum decides WHICH names; this decides the split.
#
#   "equal"   1/N. The control, and the default.
#   "invvol"  proportional to 1/sigma_i. No covariance matrix, nothing to invert or go singular.
#   "minvar"  long-only minimum variance on a Ledoit-Wolf shrunk covariance, weight-capped.
#   "sharpe_lw"  long-only MAX TRAILING SHARPE on a shrunk covariance, floored AND capped.
#
# "equal", "invvol" and "minvar" FORECAST NOTHING. They use only covariance structure, because that is
# the part estimable from 250 observations -- optimisers that lever into expected-return estimates are
# the reason 1/N beat fourteen of them in DeMiguel, Garlappi & Uppal (2009).
#
# "sharpe_lw" IS THE EXCEPTION, and it is an exception on purpose: a Sharpe ratio has `w.mu` in its
# numerator, so it DOES forecast returns, from a trailing mean that is far noisier than the covariance
# it divides by. On a momentum-selected book it is effectively a second momentum bet. It is provided so
# the idea can be MEASURED rather than argued about; it is not a recommendation. Treat any result from
# it as provisional until it has cleared the bar below.
#
# Equal weight is the benchmark to beat, not a placeholder, and any change here must beat it NET OF
# COSTS on more than one universe. Measured so far: minvar beat 1/N on return in 0 of 40 arms
# (9 universes x 5 caps) outside a single universe.
WEIGHTING = _ov("weighting", "equal")            # "equal" | "invvol" | "minvar" | "sharpe_lw"
WEIGHT_LOOKBACK = _ov("weight_lookback", 252)    # sessions of returns used to estimate risk (causal)
WEIGHT_MIN_OBS = _ov("weight_min_obs", 60)       # fewer returns than this -> that name takes 1/N
WEIGHT_CAP_MULT = _ov("weight_cap_mult", 3.0)    # "minvar" cap, as a MULTIPLE of equal weight
COV_SHRINKAGE = _ov("cov_shrinkage", True)       # Ledoit-Wolf shrink; False = raw sample covariance
WEIGHT_FLOOR_MULT = _ov("weight_floor_mult", 0.5)  # "sharpe_lw" FLOOR, as a multiple of equal
                                                 # weight. Stops the optimiser de-selecting
                                                 # a name the selection stage chose.

if str(WEIGHTING).lower() not in ("equal", "minvar", "invvol", "sharpe_lw"):
    raise ValueError(f"WEIGHTING must be 'equal', 'invvol', 'minvar' or 'sharpe_lw', "
                     f"got {WEIGHTING!r}")
if float(WEIGHT_CAP_MULT) < 1.0:
    raise ValueError(f"WEIGHT_CAP_MULT must be >= 1.0 (1.0 == equal weight), got {WEIGHT_CAP_MULT!r}")
if not (0.0 < float(WEIGHT_FLOOR_MULT) <= 1.0):
    raise ValueError(f"WEIGHT_FLOOR_MULT must be in (0, 1] (1.0 == equal weight), "
                     f"got {WEIGHT_FLOOR_MULT!r}")
if float(WEIGHT_FLOOR_MULT) > float(WEIGHT_CAP_MULT):
    raise ValueError(f"WEIGHT_FLOOR_MULT ({WEIGHT_FLOOR_MULT}) cannot exceed WEIGHT_CAP_MULT "
                     f"({WEIGHT_CAP_MULT}) -- no feasible weight exists")


# --- DRAWDOWN BREAKER: full exit to cash when the BOOK falls this far from its own peak NAV ---------
# Measured on the PORTFOLIO, not the index, and evaluated daily on marked NAV -- so it is path
# dependent and cannot be precomputed from signals. Honoured by BOTH execution engines (see
# breaker.DrawdownBreaker); dd_sweep.py / dd_breaker.py run the same state machine, so a sweep result
# and a configured run mean the same thing.
#
#   DD_EXIT = None   breaker OFF (the default, and the baseline every variant must be compared against)
#   DD_EXIT = 0.15   flatten the whole book at the next open once NAV is -15% from its peak
#
# BEFORE YOU TURN THIS ON: on this book a breaker has never paid for itself. Across smallcap250 and
# nifty500, 0 of 9 configurations improved BOTH drawdown and CAGR; a -15% exit cost ~15pp of CAGR to
# save ~4pp of drawdown, because it sells after the loss, near the low, and then has to time the way
# back. dd_frontier.py (a standing deployment cap) beat it every time. Sweep YOUR universe first:
#     python dd_sweep.py --triggers 10,15,20 --reentry gate,recover,both
DD_EXIT = _ov("dd_exit", None)          # positive fraction (0.15 = -15%), or None to disable
DD_REENTRY = _ov("dd_reentry", "both")  # "gate" (trend gate up) | "recover" (index off its low) | "both"
DD_RECOVER = _ov("dd_recover", 0.05)    # "recover"/"both": how far the SIGNAL index must rise off its
                                        # lowest close since the halt before trading resumes

if DD_EXIT is not None:
    if not (0.0 < float(DD_EXIT) < 1.0):
        raise ValueError(f"DD_EXIT must be a positive fraction below 1 (0.15 = -15%) or None, "
                         f"got {DD_EXIT!r}. A value >= 1 would never trigger; <= 0 halts immediately.")
    if str(DD_REENTRY).lower() not in ("gate", "recover", "both"):
        raise ValueError(f"DD_REENTRY must be 'gate', 'recover' or 'both', got {DD_REENTRY!r}")
    if not (0.0 <= float(DD_RECOVER) < 1.0):
        raise ValueError(f"DD_RECOVER must be in [0, 1), got {DD_RECOVER!r}")
# STAIRCASE FLAG (tradability gate, qbeast_exec.bands.StaircaseFlag).
# A stock climbing its upper circuit in steps -- +band%, +band%, +band% -- is an operator ladder, not
# price discovery. Micro caps are where it lives. It matters because MOMENTUM RANKS THESE NAMES TOP: a
# run of locked up-sessions is arithmetically the strongest momentum in the universe, so the strategy
# wants to buy exactly the thing whose price is not real and whose upper lock has no sellers anyway.
# While flagged, BUY is refused; SELL stays open so an existing position can always exit.
#
# Measured on 888,022 micro-cap symbol-days: 531 closes at/above the upper band (0.060%), and only
# 5 runs of 3+ consecutive sessions in the whole history. On THIS data it is a cheap safety rail, not
# a material return driver -- see reports/<universe>/staircase_*.csv for what it actually caught.
USE_STAIRCASE_FLAG = _ov("use_staircase_flag", True)
# --- staircase / circuit tradability gate (QBEAST circuit flags v2.4.1) ----------------------------
# A tag needs START_DAYS consecutive same-direction ladder days and clears only after END_DAYS NORMAL
# sessions - not locked, not single-price, and with volume >= NORMAL_VOL x the clean-session baseline.
# While tagged there is NO BUY, NO SELL AND NO EXIT; the position is still marked to market.
#
# WINDOW and MIN_HITS are gone: the old detector counted band closes in a trailing window and let the
# tag evaporate when the window moved on, with no requirement that normal trading had resumed.
STAIRCASE_START_DAYS = _ov("staircase_start_days", 5)    # S - consecutive ladder days to tag
STAIRCASE_END_DAYS = _ov("staircase_end_days", 2)        # E - normal days to untag
STAIRCASE_NORMAL_VOL = _ov("staircase_normal_vol", 0.5)  # a normal day needs this x the baseline
STAIRCASE_VOL_LOOKBACK = _ov("staircase_vol_lookback", 20)   # clean sessions in that baseline
STAIRCASE_MIN_PRICE = _ov("staircase_min_price", 5.0)    # below this, one tick IS a band-sized move
STAIRCASE_CHRONIC = _ov("staircase_chronic", True)       # also gate on chronic illiquidity


def staircase_config():
    """The v2.4.1 tradability config for this universe - the ONE definition of untradeable."""
    from qbeast_exec import StaircaseConfig
    return StaircaseConfig(start_confirm_days=int(STAIRCASE_START_DAYS),
                           end_confirm_days=int(STAIRCASE_END_DAYS),
                           normal_vol_frac=float(STAIRCASE_NORMAL_VOL),
                           volume_lookback=int(STAIRCASE_VOL_LOOKBACK),
                           min_price=float(STAIRCASE_MIN_PRICE),
                           chronic=bool(STAIRCASE_CHRONIC))


# PRICE BANDS / CIRCUIT LOCKS (Tier 1 tradability gate, qbeast_exec.bands).
# A stock locked at its circuit trades on ONE side only: upper -> cannot BUY, lower -> cannot SELL.
# MEASURED on these universes (1.85M symbol-days): real locks are ~0.001% -- about 21 events in total --
# so this does NOT move backtest P&L (a naive high==low detector over-counts by ~1000x because flat bars
# are nearly always illiquid non-trades or penny-stock tick artifacts). Default OFF for the research
# backtest because it costs an extra OHLCV pass for no measurable change; turn ON for live/paper realism.
USE_PRICE_BANDS = _ov("use_price_bands", True)   # research backtest: OFF (measured identical to 4dp)
USE_PRICE_BANDS_LIVE = _ov("use_price_bands_live", True)   # live/paper: ON. A lock WILL
                               # eventually occur in real trading and an order must not be assumed
                               # filled, so the forward path always carries the guard.
POINT_IN_TIME = USE_MEMBERSHIP  # backward-compat alias -> edit USE_MEMBERSHIP (DATA section) above, not this
REBALANCE_FREQ = _ov("rebalance_freq", "monthly")   # re-selection cadence: monthly | weekly | quarterly

# --- costs / tax / cash (Indian large-cap delivery trading) ---------------------------------------
INITIAL_CAPITAL = _ov("initial_capital", 10_00_000.0)
# A return is a RATIO, so it needs a non-zero base. With INITIAL_CAPITAL = 0 the book can never buy a
# share, NAV stays 0 for every day, and CAGR = (0/0)**(1/yrs) - 1 = nan -- undefined, NOT zero. The run
# would complete and hand back a dashboard reading "CAGR nan%, maxDD nan%, Calmar nan", which is a worse
# outcome than refusing: nan propagates silently into any comparison it touches. So refuse here, at the
# top, rather than 2 minutes later on a division buried in a report.
if not (isinstance(INITIAL_CAPITAL, (int, float)) and INITIAL_CAPITAL > 0):
    raise ValueError(
        f"config.INITIAL_CAPITAL must be a positive number, got {INITIAL_CAPITAL!r}.\n"
        f"  A percentage return is undefined on a zero base -- 0/0 is nan, not 0 -- so every return,\n"
        f"  drawdown and ratio in the reports would be nan. Set a real starting capital\n"
        f"  (e.g. 1_000_000.0), or override 'initial_capital' for this universe in universes.py.")
COST_PCT = _ov("cost_pct", 0.0011)   # BLENDED brokerage + STT + exchange + stamp + GST, per side
                               # (used when CHARGES_MODE == "blended"; see the EXECUTION section below)
SLIP_MIN = _ov("slip_min", 0.0005)   # rank slippage: the MOST liquid stock pays this
SLIP_MAX = _ov("slip_max", 0.0025)   # rank slippage: the LEAST liquid stock pays this
# Indian listed-equity capital-gains tax: DATE-DEPENDENT (Finance Act 2024, effective 23-Jul-2024).
# The rate on a realised gain is set by its SALE (transfer) date; the LTCG exemption is an annual (per-FY)
# allowance. Base statutory rates under s.111A / s.112A (surcharge & cess NOT added).
CGT_REGIME_CHANGE = pd.Timestamp("2024-07-23")
STCG_RATE_OLD, STCG_RATE_NEW = 0.15, 0.20      # short-term (held < 365d): 15% before, 20% on/after 23-Jul-2024
LTCG_RATE_OLD, LTCG_RATE_NEW = 0.10, 0.125     # long-term  (held >= 365d): 10% before, 12.5% on/after 23-Jul-2024
LTCG_EXEMPTION_OLD, LTCG_EXEMPTION_NEW = 100_000.0, 125_000.0   # annual LTCG exemption: Rs1.00L (to FY2023-24), Rs1.25L (from FY2024-25)
# HOLDING PERIOD: s.2(42A) makes a listed equity share SHORT-term when held "not more than 12 MONTHS",
# so long-term needs MORE than 12 calendar months. That is NOT 365 days -- a 12-month span containing
# 29-Feb is 366 days, so a 365-day rule grants LTCG a day or two EARLY (and so under-states tax).
LTCG_HOLD_MONTHS = 12          # the statutory definition (calendar months)
LTCG_HOLD_DAYS = 365           # legacy day-count; used ONLY when LTCG_USE_MONTHS = False
LTCG_USE_MONTHS = True         # True -> calendar months (correct); False -> the old 365-day rule


def ltcg_qualifies_from(buy_date):
    """First sale date on which a lot bought on `buy_date` is LONG-term.
    Calendar-month arithmetic handles month lengths and leap days (31-Jan+12m -> 31-Jan;
    29-Feb-2024+12m -> 28-Feb-2025). Long-term starts the day AFTER the 12-month anniversary."""
    b = pd.Timestamp(buy_date).normalize()
    if not LTCG_USE_MONTHS:
        return b + pd.Timedelta(days=LTCG_HOLD_DAYS)
    return b + pd.DateOffset(months=LTCG_HOLD_MONTHS) + pd.Timedelta(days=1)


def is_long_term(buy_date, sell_date) -> bool:
    """True if the lot is a LONG-term capital asset when sold on `sell_date`."""
    return pd.Timestamp(sell_date).normalize() >= ltcg_qualifies_from(buy_date)
# single-rate aliases (some display code imports these) -> the CURRENT (post-23-Jul-2024) regime.
STCG_RATE, LTCG_RATE, LTCG_EXEMPTION = STCG_RATE_NEW, LTCG_RATE_NEW, LTCG_EXEMPTION_NEW


def cgt_rates(sale_date):
    """(stcg_rate, ltcg_rate) applicable to a gain REALISED on `sale_date` (the 23-Jul-2024 change)."""
    new = pd.Timestamp(sale_date) >= CGT_REGIME_CHANGE
    return (STCG_RATE_NEW, LTCG_RATE_NEW) if new else (STCG_RATE_OLD, LTCG_RATE_OLD)


def ltcg_exemption_for_fy(fy):
    """Annual LTCG exemption for financial year `fy` (fy = the starting calendar year; FY2024-25 -> 2024)."""
    return LTCG_EXEMPTION_NEW if fy >= 2024 else LTCG_EXEMPTION_OLD
CASH_YIELD = _ov("cash_yield", 0.045)   # net-of-tax yield on idle cash (liquid fund / T-bill)

# --- EXECUTION: which engine keeps the book, and where charges/slippage/tax come from ---------------
# SINGLE SOURCE. Charges, slippage and capital-gains tax are computed by the shared `qbeast_exec`
# package - the same code every strategy on the team uses - NOT re-implemented per file. The three
# builders below are the only places that translate these settings into qbeast_exec configs; engine.py,
# nautilus_run.py and the research scripts all call them instead of hard-coding a rate.
#
# EXEC_ENGINE decides WHO KEEPS THE LEDGER (positions, orders, fills, cash, books):
#   "nautilus"  NautilusTrader - a real order/position/cash model, an INR cash account with no borrowing,
#               and proper order + trade books. This is what live/paper trading runs on, so the research
#               backtest running on it too means one execution path, not two.
#   "internal"  engine.Ledger - the transparent in-repo cash simulation. Faster, and it is the only path
#               that models the after-tax sizing loop, so tax-sensitive research still needs it.
# Reconciled to within 0.15% of each other; see NAUTILUS_INTEGRATION.md.
EXEC_ENGINE = _ov("exec_engine", "nautilus")

# --- Nautilus cash budget -------------------------------------------------------------------------
# A market-on-open order is sized at the PREVIOUS close, because that is all it can know, but it fills
# at the next open. This is the cushion the cash budget keeps for that gap, applied in the adverse
# direction on both legs (buys cost more, sells raise less).
#
# 0.01 is MEASURED, not chosen: across 397,051 close-to-open gaps on smallcap250 the mean |gap| is
# 1.01% (buy q75 +0.83%, sell q10 -1.06%). The previous cushion was 3*COST_PCT = 0.45%, under half the
# mean, and a routine gap therefore overdrew the account - which a Nautilus CASH account answers by
# ABORTING the whole run (seen 2021-12-02 on smallcap250, balance -3,585.24).
#
# Budgeting for the 99th percentile (5.39%) would leave the book permanently under-invested. The aim is
# not to make an overdraft impossible but to make it rare enough never to cost a run: under-investing
# slightly is a bounded cost, an abort destroys the result. A large-cap universe gaps less and can
# lower this; a microcap universe should raise it.
NAUTILUS_GAP_ALLOW = 0.01

# CHARGES_MODE: "blended"  one COST_PCT rate per side (reproduces every published number here)
#               "itemised" qbeast_exec's real schedule - STT, exchange, SEBI, stamp duty, GST, DP -
#                          which is more accurate but WILL move the P&L. Switch deliberately.
CHARGES_MODE = _ov("charges_mode", "itemised")

# SLIPPAGE_MODE: "rank"    per-stock rate interpolated by liquidity rank, SLIP_MIN..SLIP_MAX (the
#                          baseline behind the published numbers)
#                "dynamic" size-aware Corwin-Schultz half-spread + sqrt impact, two-sided per fill.
#                          Needs HIGH/LOW, and gives a capacity curve. See slippage_model.py.
#                "fixed" / "none"
SLIPPAGE_MODE = _ov("slippage_mode", "dynamic")


def charges_config():
    """The ChargesConfig for this universe - the ONE definition of what a trade costs."""
    from qbeast_exec import ChargesConfig
    if CHARGES_MODE == "blended":
        return ChargesConfig.blended(rate=COST_PCT)
    if CHARGES_MODE == "itemised":
        return ChargesConfig.equity_delivery()
    raise ValueError(f"CHARGES_MODE must be 'blended' or 'itemised', got {CHARGES_MODE!r}")


def slippage_config():
    """The SlippageConfig for this universe - the ONE definition of what a fill slips."""
    from qbeast_exec import SlippageConfig
    return SlippageConfig(mode=SLIPPAGE_MODE, rank_min=SLIP_MIN, rank_max=SLIP_MAX)


def tax_config():
    """The TaxConfig for this universe - the ONE definition of the capital-gains rules. Mirrors the
    statutory constants above so the two can never drift apart."""
    from qbeast_exec import TaxConfig
    return TaxConfig(
        regime_change=CGT_REGIME_CHANGE,
        stcg_rate_old=STCG_RATE_OLD, stcg_rate_new=STCG_RATE_NEW,
        ltcg_rate_old=LTCG_RATE_OLD, ltcg_rate_new=LTCG_RATE_NEW,
        ltcg_exemption_old=LTCG_EXEMPTION_OLD, ltcg_exemption_new=LTCG_EXEMPTION_NEW,
        ltcg_hold_months=LTCG_HOLD_MONTHS,
        ltcg_hold_days=None if LTCG_USE_MONTHS else LTCG_HOLD_DAYS,
    )


# --- output ---------------------------------------------------------------------------------------
# EVERY GENERATED OUTPUT IS PARTITIONED BY UNIVERSE.  reports/<universe>/
#
# Switching UNIVERSE switches the PRODUCT: different names, different liquidity, different capacity and
# a different benchmark. A shared reports/ folder meant each switch silently overwrote the previous
# universe's order book, trade book, dashboard and equity curve with numbers from a different strategy --
# and the files gave no clue which universe they came from. Comparing two universes then meant re-running
# one of them, and any file left open in Excel was from whichever universe happened to run last.
#
# So: one folder per universe, the same partitioning paper_trading/ already uses (prices/<u>/, state/<u>/,
# reports/<u>/). Nothing else changes -- every script writes to OUT_DIR as before and lands in the right
# place automatically.
REPORTS_ROOT = Path(__file__).resolve().parent / "reports"
OUT_DIR = REPORTS_ROOT / str(UNIVERSE).strip().lower()
OUT_DIR.mkdir(parents=True, exist_ok=True)


# --- interpreter guard, driven by the settings above ----------------------------------------------
# Deliberately checks only what THIS configuration needs, so the message is always true: switch the gate
# to "dma" and patsy stops being required; switch EXEC_ENGINE to "internal" and nautilus_trader does.
# Anything else a particular script needs, that script asks for itself -- e.g. config.require("plotly").
# `patsy` used to be required here, for the removed strategy's p-spline trend gates. Nothing in the
# framework needs it, so the only conditional requirement left is the execution engine. A strategy
# that needs a library of its own asks for it itself: `config.require("statsmodels", why=...)`.
_needed = ["nautilus_trader"] if str(EXEC_ENGINE).lower() == "nautilus" else []
require(*_needed, why=f"EXEC_ENGINE={EXEC_ENGINE!r}")


# --- survivorship: refuse a window that starts before the point-in-time data ------------------------
# BACKTEST_START must be on or after the universe's FIRST reconstitution. Earlier dates reuse that first
# list retroactively, so those years hold only the names that SURVIVED to it -- survivorship bias,
# confined to exactly the early years nobody re-checks.
#
# This was a printed warning for months. In a single day it let four different universes run with a
# mis-set window (up to 6.25 years of look-ahead) because the warning scrolled past in the backtest
# output. A comment cannot enforce an invariant and a warning cannot either, so it is a hard failure at
# import: every script that touches config gets the check for free.
#
# Deliberate exception (a short pre-history run, reproducing an old number):
#     set REGIMESWITCH_ALLOW_LOOKAHEAD=1
if USE_MEMBERSHIP and not _os.environ.get("REGIMESWITCH_ALLOW_LOOKAHEAD"):
    from qbeast_exec.survivorship import Membership as _Membership
    from qbeast_exec.survivorship import SurvivorshipConfig as _SurvCfg
    from qbeast_exec.survivorship import assert_no_look_ahead as _assert_no_look_ahead

    _memb_check = _Membership(MEMBERSHIP_FILE, config=_SurvCfg(strict=True),
                              extra_dirs=[DATA_DIR, Path(DATA_DIR).parent,
                                          Path(MEMBERSHIP_FILE).parent])
    try:
        _assert_no_look_ahead(_memb_check, BACKTEST_START)
    except ValueError as _e:
        raise ValueError(
            f"{_e}\n"
            f"    universe   : {UNIVERSE}\n"
            f"    set BACKTEST_START = pd.Timestamp(\"{_memb_check.first_date.date()}\") in config.py,\n"
            f"    or set REGIMESWITCH_ALLOW_LOOKAHEAD=1 to run anyway and label the result "
            f"survivorship-biased.") from None
    del _memb_check


# ===================================================================================================
# ADDED BY Backtest_Modular  (re-applied by sync_config.py after every pull)
# ===================================================================================================
# --- WHAT THE STAIRCASE GATE ACTUALLY DOES -------------------------------------------------------
# Until Backtest_Modular the flag was consulted ONLY at fill time: a flagged name won one of the TOP_N
# slots, its buy was then refused by the ledger, and the slot's cash sat idle - the book ran N-1 names
# and the next-ranked tradeable name never got in. Refusing the fill protects you from owning the
# thing; removing it from the CANDIDATE POOL also lets you own something else instead.
#
# This matters most for a momentum ranker, because a run of locked +5% sessions is arithmetically the
# strongest momentum in the universe. The signal and the gate point in opposite directions, and
# whichever runs LAST wins. With this True the gate runs FIRST.
STAIRCASE_EXCLUDE_FROM_POOL = _ov("staircase_exclude_from_pool", True)

# BLOCKING EXITS IS A DIFFERENT QUESTION, and the answer is not symmetric.
# qbeast_exec's own default is block_sell=False, with a stated reason: an UPPER circuit lock has no
# sellers, so it is a state you can always get OUT of - demand is what defines it - while refusing the
# exit traps capital in the one security just identified as an operator ladder rather than a market.
# Turning this on makes the book hold a manipulated name until the pattern clears, which is the
# opposite of what the gate is for. Available because it was asked for; OFF because the asymmetry is
# real.
WARMUP_BARS = _ov("warmup_bars", 400)

# OUTPUTS GO IN THIS FOLDER'S OWN reports/, not shared/reports/. Backtest_Core computes REPORTS_ROOT
# from config.py's own location, and here config.py lives in shared/ - so the unpatched value buries
# every output one level down. Re-pointed at the folder root, which is also what keeps runs from this
# folder out of Backtest_Core entirely.
# OUTPUTS. An installed package must not write inside site-packages, so the root is resolved in this
# order and the first that answers wins:
#   1. $QBEAST_BACKTEST_REPORTS      explicit, and what a scheduled run should set
#   2. <project root>/reports        when running from a source checkout (the normal case)
#   3. ./reports                     last resort, relative to the working directory
_env = _os.environ.get("QBEAST_BACKTEST_REPORTS")
if _env:
    REPORTS_ROOT = Path(_env)
else:
    _here = Path(__file__).resolve()
    # .../<project>/src/qbeast_backtest/config.py  ->  <project>/reports
    _proj = _here.parent.parent.parent
    REPORTS_ROOT = (_proj / "reports") if (_proj / "pyproject.toml").exists()         else Path.cwd() / "reports"
OUT_DIR = REPORTS_ROOT / str(UNIVERSE).strip().lower()
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Where this run's staircase log is written (stage1_data/staircase_log.py).
STAIRCASE_LOG_DIR = OUT_DIR / "staircase"
