"""
qbeast_backtest/membership.py
--------------------------
This project's binding to the SHARED survivorship control.

The rule, the guards and the audit now live in `qbeast_exec.survivorship`, so every strategy on the
team applies point-in-time membership the same way and any fix lands everywhere at once. What stays
here is only the part that is specific to THIS project: reading the universe's membership path out of
config, and the alias table for the ticker spellings in our data folders.

The public names are unchanged -- `Membership(available=...)`, `.eligible(date)`, `load_membership()` --
so every existing call site works untouched.

WHY THIS MATTERS (the short version; the long one is in qbeast_exec/survivorship.py)
    Selecting from today's index members on historical dates picks the survivors using information from
    the end of the window. Measured on this project it lifted a LightGBM t-statistic from 1.42 to 2.54
    while pushing real momentum down from 2.63 to 1.66.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from qbeast_backtest.config import DATA_DIR, MEMBERSHIP_FILE, USE_MEMBERSHIP
from qbeast_exec.survivorship import DEFAULT_ALIAS, DEFAULT_JUNK, SurvivorshipConfig
from qbeast_exec.survivorship import Membership as _SharedMembership
from qbeast_exec.survivorship import load_membership as _shared_load

# membership ticker -> our data filename.  (*) = merger proxy, not a clean rename.
# Seeded from the shared defaults so a spelling fixed for the team arrives here too; add project-only
# entries below.
ALIAS = dict(DEFAULT_ALIAS)
JUNK = set(DEFAULT_JUNK)                        # index self-reference / bond artefacts in the feed


def _search_dirs() -> list[Path]:
    """Where to look when the configured path is wrong: the data folder and its ancestors.

    SOURCE often points at a sub-folder (…/Clean_N100) while the membership CSV sits a level or two up
    beside the raw data."""
    d = Path(DATA_DIR)
    return [d, d.parent, d.parent.parent, Path(MEMBERSHIP_FILE).parent]


def _config() -> SurvivorshipConfig:
    return SurvivorshipConfig(alias=ALIAS, junk=frozenset(JUNK), strict=True)


def load_membership() -> list[tuple[pd.Timestamp, set[str]]]:
    """[(effective_date, {members mapped to our tickers})], ascending.

    EMPTY when config.USE_MEMBERSHIP is False -> every priced symbol is eligible. When it is True but
    the file is missing this RAISES (see the shared module): a warning would let a survivorship-biased
    run finish while still reporting as bias-controlled."""
    if not USE_MEMBERSHIP:
        return []
    return _shared_load(MEMBERSHIP_FILE, _config(), extra_dirs=_search_dirs())


class Membership(_SharedMembership):
    """As-of eligible universe for THIS project's configured universe.

    A pass-through when USE_MEMBERSHIP is False: `eligible()` returns the whole priced universe, so call
    sites need no branching -- but `enabled` is then False and `report()` says the results are
    survivorship-biased."""

    def __init__(self, available: set[str] | None = None) -> None:
        super().__init__(path=MEMBERSHIP_FILE if USE_MEMBERSHIP else None,
                         available=available, config=_config(), extra_dirs=_search_dirs())
