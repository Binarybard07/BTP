r"""
qbeast_exec/survivorship.py
---------------------------
Point-in-time index membership: the survivorship-bias control, as a library.

THE BIAS, IN ONE PARAGRAPH
    Take today's index constituents and backtest them over ten years. Every name in that list survived
    ten years -- it did not get delisted, acquired, or demoted for collapsing. You have selected the
    winners with information from the end of the window and then measured how well the winners did. On
    THIS codebase that bias lifted a LightGBM trend model's t-statistic from 1.42 to 2.54 while pushing
    genuine 12-1 momentum DOWN from 2.63 to 1.66: it manufactures skill that is not there, and hides
    skill that is. It is the largest single source of fake alpha we have measured.

THE FIX
    On each date, select only from the names that were ACTUALLY in the index on that date, read from
    exchange reconstitution records. That is all this module does, but it does it with the guards that
    matter:

      * a missing membership file RAISES, it does not warn. A warning scrolls past in a wall of
        backtest output and the run still prints a headline that reads as bias-controlled.
      * a window starting BEFORE the first reconstitution is reported as look-ahead, because the usual
        implementation silently reuses the earliest list for every earlier date -- which is exactly the
        bias again, confined to the years nobody re-checks.
      * `coverage()` quantifies the PARTIAL fix: constituents you have no prices for are still absent,
        so an honest run states how much of the real index it could actually hold.

STANDALONE
    Depends only on pandas and the standard library. No project imports, no config module: pass the
    file path in. Use it from any strategy project.

FILE FORMAT
    A CSV with one row per reconstitution:

        effective_date,symbols
        2016-04-29,"ACC,ADANIENT,AMBUJACEM,..."

    `effective_date` is any format pandas can parse; `symbols` is comma-separated. Extra columns
    (index_type, inclusions, exclusions, symbol_count) are ignored, so exchange exports work as-is.

QUICK START
    from qbeast_exec import Membership

    memb = Membership("E:/data/Nifty100_membership.csv", available=set(prices.columns))
    for t in calendar:
        for s in memb.eligible(t):        # only names really in the index on t
            ...
    print(memb.report())                  # coverage + look-ahead audit, for the run log
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# Ticker aliases seen in Indian index reconstitution feeds: membership symbol -> data filename.
# "(*)" marks a MERGER PROXY -- the surviving entity, not a clean rename. Those are a judgement call,
# so they are exposed as a default you can override rather than buried in the parser.
DEFAULT_ALIAS: dict[str, str] = {
    "SESA": "VEDL", "STERLITE": "VEDL", "CAIRN": "VEDL",          # (*) -> Vedanta
    "ICICI": "ICICIBANK", "IDFC": "IDFCFIRSTB",                   # (*) surviving-entity proxies
    "BHARTI": "BHARTIARTL", "MOTHERSUMI": "MOTHERSON", "TECH": "TECHM",
    "ZOMATO": "ETERNAL", "JIOFINAN": "JIOFIN", "BOSCH-HCIL": "BOSCHLTD",
    "HEALTHCARE": "MAXHEALTH",
}

# Index self-references and bond artefacts that appear in constituent exports but are not tradeable.
DEFAULT_JUNK: frozenset[str] = frozenset({"NIFTY", "722HPCL29"})


def normalise(symbol: str) -> str:
    """Ticker normalisation: strip, drop internal spaces, upper-case."""
    return str(symbol).strip().replace(" ", "").upper()


@dataclass
class SurvivorshipConfig:
    """How to read a membership file.

    strict=True (the default) makes a missing file an ERROR. Set it to False only when you intend to run
    WITHOUT point-in-time control and are prepared to label the results survivorship-biased."""

    alias: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ALIAS))
    junk: frozenset[str] = DEFAULT_JUNK
    strict: bool = True
    date_column: str = "effective_date"
    symbols_column: str = "symbols"


def find_membership_file(path, extra_dirs=()) -> Path | None:
    """Resolve a membership CSV: the given path, else the same filename in nearby data roots.

    A data folder is often a sub-directory (…/Clean_N100) while the membership file sits a level or two
    up beside the raw data, so the parents of `path` are searched too. Returns None if nothing matches."""
    p = Path(path)
    candidates = [p]
    for d in list(extra_dirs) + [p.parent]:
        d = Path(d)
        candidates += [d / p.name, d.parent / p.name, d.parent.parent / p.name]
    for c in candidates:
        try:
            if c.exists() and c.is_file():
                return c
        except OSError:                                            # unreadable path -> just skip it
            continue
    return None


def load_membership(path, config: SurvivorshipConfig | None = None,
                    extra_dirs=()) -> list[tuple[pd.Timestamp, set[str]]]:
    """Read a membership file into [(effective_date, {tickers}), ...], sorted ascending.

    Raises FileNotFoundError when the file is missing and config.strict is True -- with the near misses
    listed, because the usual cause is a path typo, and the usual consequence of continuing is a
    survivorship-biased run that still reports as controlled."""
    cfg = config or SurvivorshipConfig()
    f = find_membership_file(path, extra_dirs)
    if f is None:
        if not cfg.strict:
            return []
        near = sorted({q.name for d in [Path(path).parent, *map(Path, extra_dirs)]
                       if d.exists() for q in d.glob("*member*.csv")})
        raise FileNotFoundError(
            f"membership file not found: {path}\n"
            + (f"    did you mean: {', '.join(near)}\n" if near else "")
            + "    Point-in-time eligibility was requested but cannot be applied. Refusing to continue:\n"
              "    running on every available symbol would be SURVIVORSHIP BIASED while still reporting\n"
              "    as bias-controlled. Fix the path, or pass SurvivorshipConfig(strict=False) to run\n"
              "    deliberately without point-in-time control.")
    m = pd.read_csv(f)
    for col in (cfg.date_column, cfg.symbols_column):
        if col not in m.columns:
            raise ValueError(f"{f}: missing column {col!r}. Found: {', '.join(map(str, m.columns))}")
    m[cfg.date_column] = pd.to_datetime(m[cfg.date_column])
    out: list[tuple[pd.Timestamp, set[str]]] = []
    for _, r in m.sort_values(cfg.date_column).iterrows():
        syms = {normalise(x) for x in str(r[cfg.symbols_column]).split(",") if str(x).strip()}
        syms -= set(cfg.junk)
        out.append((r[cfg.date_column], {cfg.alias.get(s, s) for s in syms}))
    return out


class Membership:
    """As-of lookup of the eligible universe on any date.

    Membership(path, available=...) with `available` set to the symbols you actually have prices for is
    the normal use: `eligible(t)` then returns exactly what you may select from on date `t`.

    Passing path=None (or SurvivorshipConfig(strict=False) with a missing file) makes this a
    PASS-THROUGH that returns the whole available universe -- so call sites need no branching. It is
    then NOT survivorship-controlled, `enabled` is False, and `report()` says so in those words."""

    def __init__(self, path=None, available: set[str] | None = None,
                 config: SurvivorshipConfig | None = None, extra_dirs=()) -> None:
        self.config = config or SurvivorshipConfig()
        self.path = path
        self.records = load_membership(path, self.config, extra_dirs) if path is not None else []
        self.dates = [d for d, _ in self.records]
        self.available = set(available) if available is not None else None
        self._warned_pre = False
        self._asked: list[pd.Timestamp] = []

    # ---------------------------------------------------------------- state
    @property
    def enabled(self) -> bool:
        """True when a real point-in-time control is in force."""
        return bool(self.records)

    @property
    def first_date(self) -> pd.Timestamp | None:
        """The first reconstitution. A backtest must not start before this."""
        return self.dates[0] if self.dates else None

    @property
    def last_date(self) -> pd.Timestamp | None:
        return self.dates[-1] if self.dates else None

    # ---------------------------------------------------------------- lookup
    def members(self, date) -> set[str]:
        """Constituents on `date`, BEFORE intersecting with what you have prices for."""
        if not self.enabled:
            return set()
        i = bisect.bisect_right(self.dates, pd.Timestamp(date)) - 1
        if i < 0:
            self._warn_pre_history(date)
        return set(self.records[max(i, 0)][1])

    def eligible(self, date) -> set[str]:
        """What you may select from on `date`: constituents ∩ symbols you have prices for."""
        d = pd.Timestamp(date)
        self._asked.append(d)
        if not self.enabled:
            return set(self.available) if self.available is not None else set()
        m = self.members(d)
        return (m & self.available) if self.available is not None else m

    def _warn_pre_history(self, date) -> None:
        """A date before the first reconstitution reuses the earliest list -- i.e. the survivors."""
        if self._warned_pre or not self.dates:
            return
        self._warned_pre = True
        first = self.dates[0]
        print(f"  [survivorship] *** LOOK-AHEAD: {pd.Timestamp(date).date()} precedes the first "
              f"reconstitution ({first.date()}). Earlier dates reuse that list retroactively, so those "
              f"years hold only names that SURVIVED to {first.date()}. Start at {first.date()}, or "
              f"label pre-{first.year} results survivorship-biased. ***", flush=True)

    # ---------------------------------------------------------------- audit
    def audit(self, start=None, end=None) -> dict:
        """Machine-readable audit. Assert on this in CI rather than trusting a printed warning.

        look_ahead_days > 0 means the window starts before the first reconstitution."""
        start = pd.Timestamp(start) if start is not None else None
        gap = 0
        if self.enabled and start is not None and start < self.dates[0]:
            gap = int((self.dates[0] - start).days)
        cov = self.coverage()
        return dict(
            enabled=self.enabled,
            path=str(self.path) if self.path else None,
            reconstitutions=len(self.records),
            first_reconstitution=self.first_date,
            last_reconstitution=self.last_date,
            window_start=start,
            window_end=pd.Timestamp(end) if end is not None else None,
            look_ahead_days=gap,
            look_ahead=bool(gap > 0),
            mean_members=float(cov["members"].mean()) if len(cov) else 0.0,
            mean_priced=float(cov["priced"].mean()) if len(cov) else 0.0,
            mean_coverage_pct=float(cov["coverage_pct"].mean()) if len(cov) else 0.0,
        )

    def coverage(self) -> pd.DataFrame:
        """How much of the real index you can actually hold, per reconstitution.

        The point-in-time fix is only as complete as your price history: a constituent you have no
        prices for is still missing from the eligible set. This is the honest measure of that gap, and
        it belongs in the run log rather than in a caveat nobody reads."""
        if not self.enabled:
            return pd.DataFrame(columns=["effective_date", "members", "priced", "coverage_pct"])
        rows = []
        for d, m in self.records:
            priced = len(m & self.available) if self.available is not None else len(m)
            rows.append(dict(effective_date=d, members=len(m), priced=priced,
                             coverage_pct=100.0 * priced / max(len(m), 1)))
        return pd.DataFrame(rows)

    def missing(self, date=None) -> set[str]:
        """Constituents you have NO prices for (on `date`, or across all dates when None)."""
        if not self.enabled or self.available is None:
            return set()
        if date is not None:
            return self.members(date) - self.available
        return set().union(*[m for _, m in self.records]) - self.available

    def report(self, start=None, end=None) -> str:
        """A short block for the run log. Says plainly when control is OFF."""
        if not self.enabled:
            return ("  [survivorship] OFF - no membership file. Every available symbol is selectable on\n"
                    "                 every date, so these results are SURVIVORSHIP BIASED. Do not\n"
                    "                 compare them with a point-in-time controlled run.")
        a = self.audit(start, end)
        out = [f"  [survivorship] ON  - {a['reconstitutions']} reconstitutions, "
               f"{a['first_reconstitution'].date()} .. {a['last_reconstitution'].date()}",
               f"                 coverage {a['mean_coverage_pct']:.1f}% "
               f"({a['mean_priced']:.0f} of {a['mean_members']:.0f} constituents priced, on average)"]
        if a["look_ahead"]:
            out.append(f"                 *** LOOK-AHEAD {a['look_ahead_days']} days: window starts "
                       f"{a['window_start'].date()}, before the first reconstitution "
                       f"{a['first_reconstitution'].date()} ***")
        n_missing = len(self.missing())
        if n_missing:
            out.append(f"                 {n_missing} constituents never priced (delisted names you "
                       f"lack history for) - the fix is partial by that much")
        return "\n".join(out)


def assert_no_look_ahead(membership: "Membership", start) -> None:
    """Raise unless the window starts on or after the first reconstitution.

    Use this in a run's integrity checks. A printed warning is not protection: this project shipped a
    look-ahead window three times in one day because the warning scrolled past."""
    if not membership.enabled:
        return
    start = pd.Timestamp(start)
    if start < membership.dates[0]:
        raise ValueError(
            f"backtest starts {start.date()} but point-in-time membership only begins "
            f"{membership.dates[0].date()} ({(membership.dates[0] - start).days} days of look-ahead). "
            f"Those dates would reuse the first reconstitution retroactively and hold only survivors. "
            f"Set the window start to {membership.dates[0].date()}.")
