r"""
qbeast_backtest/portfolio.py
-------------------------
Cross-sectional weighting inside the deployed sleeve: how much of each held name, once the gate has
decided HOW MUCH to deploy and momentum has decided WHICH names.

WHAT THIS REPLACES
    `strategy.target_weights` split the sleeve equally across the top-N. Equal weight is not a
    placeholder -- it is a genuinely hard benchmark. DeMiguel, Garlappi & Uppal (2009) tested fourteen
    optimisation models across several datasets and none beat 1/N out of sample consistently, because
    optimisers lever into estimation error in expected returns.

    So NOTHING HERE ESTIMATES EXPECTED RETURNS. Both schemes use only the covariance structure, which
    is the part that can actually be estimated from 250 observations. Momentum has already chosen the
    names; this only decides the split between them.

SCHEMES
    "equal"   1/N across the sleeve. The control, and the default.
    "invvol"  weight proportional to 1/sigma_i. Needs only each name's own volatility -- no covariance
              matrix, no matrix inversion, nothing to go singular. The robust first step.
    "minvar"  long-only minimum variance: min w'Sw subject to sum(w)=1, 0 <= w <= cap. Uses the FULL
              covariance with Ledoit-Wolf shrinkage, because the sample covariance of 20 assets on 250
              observations is badly conditioned and its inverse amplifies exactly the noise the
              optimiser then exploits.

WHY A WEIGHT CAP IS NOT OPTIONAL
    Long-only minimum variance concentrates. Left alone it will put most of the sleeve into the two or
    three lowest-volatility names, which (a) is a bet on the covariance estimate being right, and
    (b) raises per-name order size in a book where slippage is ~78% of execution cost and grows with
    the square root of participation. The cap is expressed as a MULTIPLE of equal weight, so it scales
    with N automatically.

CAUSALITY
    Every estimate uses returns up to and INCLUDING `date`, never past it. Weights decided on t fill at
    t+1's open, exactly as the momentum signal does.

DETERMINISM
    Both schemes are deterministic -- no seeds, no stochastic search. The same inputs give the same
    weights every run, which is what the rest of this project reproduces on.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SCHEMES = ("equal", "invvol", "minvar", "sharpe_lw")


def _returns(closes: pd.DataFrame, symbols, date, lookback: int) -> pd.DataFrame:
    """Daily simple returns for `symbols`, the `lookback` sessions ending ON `date` inclusive.

    Slicing by label and then tailing is deliberate: it can only ever see rows at or before `date`, so
    no reordering or reindexing accident upstream can leak a future bar in."""
    cols = [s for s in symbols if s in closes.columns]
    if not cols:
        return pd.DataFrame()
    px = closes.loc[:pd.Timestamp(date), cols].tail(int(lookback) + 1)
    return px.pct_change().iloc[1:]


def _split_by_history(rets: pd.DataFrame, symbols, min_obs: int):
    """(names with enough history, names without). Short-history names are weighted equally rather than
    guessed at: a volatility from 12 observations is not an estimate, and dropping the name silently
    would change WHICH stocks the strategy holds -- a weighting choice must never do that."""
    have, short = [], []
    for s in symbols:
        n = int(rets[s].notna().sum()) if s in rets.columns else 0
        (have if n >= min_obs else short).append(s)
    return have, short


def inverse_vol(closes, symbols, date, lookback: int = 252, min_obs: int = 60) -> dict:
    """w_i proportional to 1 / sigma_i, normalised to sum to 1.

    No covariance matrix and no inversion, so there is nothing to go singular or to amplify. This is
    the honest first question: does ANY risk-aware weighting beat 1/N here?"""
    syms = list(symbols)
    if not syms:
        return {}
    rets = _returns(closes, syms, date, lookback)
    have, short = _split_by_history(rets, syms, min_obs)
    if not have:
        return {s: 1.0 / len(syms) for s in syms}

    sd = rets[have].std()
    sd = sd.replace(0.0, np.nan)
    inv = 1.0 / sd
    inv = inv.replace([np.inf, -np.inf], np.nan).dropna()
    if inv.empty:
        return {s: 1.0 / len(syms) for s in syms}

    # short-history names take the equal-weight share; the rest split what is left by inverse vol
    eq = 1.0 / len(syms)
    budget = 1.0 - eq * len(short)
    w = (inv / inv.sum()) * budget
    out = {s: float(w.get(s, 0.0)) for s in have}
    out.update({s: eq for s in short})
    return _normalise(out)


def min_variance(closes, symbols, date, lookback: int = 252, min_obs: int = 60,
                 cap_mult: float = 3.0, shrink: bool = True) -> dict:
    """Long-only minimum variance: min w'Sw, sum(w)=1, 0 <= w_i <= cap_mult/N.

    S is Ledoit-Wolf shrunk towards a scaled identity. On 20 assets and 250 observations the sample
    covariance is poorly conditioned; its inverse magnifies the smallest, least reliable eigenvalues,
    and a minimum-variance optimiser is precisely a machine for finding them. Shrinkage is the standard
    fix and it is not optional here.

    Falls back to inverse-vol -- never to silence -- if the solver fails or the matrix is unusable."""
    syms = list(symbols)
    if not syms:
        return {}
    rets = _returns(closes, syms, date, lookback)
    have, short = _split_by_history(rets, syms, min_obs)
    if len(have) < 2:
        return inverse_vol(closes, syms, date, lookback, min_obs)

    r = rets[have].dropna(how="any")
    if len(r) < min_obs:
        r = rets[have].fillna(0.0)                     # keep the panel rectangular for the estimator
    cov = _covariance(r, shrink)
    if cov is None:
        return inverse_vol(closes, syms, date, lookback, min_obs)

    n = len(syms)
    eq = 1.0 / n
    budget = 1.0 - eq * len(short)                     # short-history names take equal weight
    cap = min(1.0, float(cap_mult) / n)
    w = _solve_min_var(cov, cap=cap / max(budget, 1e-9))
    if w is None:
        return inverse_vol(closes, syms, date, lookback, min_obs)

    out = {s: float(w[i] * budget) for i, s in enumerate(have)}
    out.update({s: eq for s in short})
    return _normalise(out)


def _project_bounds(w: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Project weights into [lo, hi] keeping the sum at 1, by water-filling.

    A clip-then-renormalise re-breaks the bounds it just enforced: renormalising after clipping scales
    every weight, pushing some back outside. This moves the excess onto the names that still have
    headroom and repeats, so the result satisfies BOTH the bounds and the budget.
    """
    w = np.clip(np.asarray(w, dtype=float), lo, hi)
    for _ in range(100):
        gap = 1.0 - w.sum()
        if abs(gap) < 1e-12:
            break
        room = (hi - w) if gap > 0 else (w - lo)
        tot = room.sum()
        if tot <= 1e-15:
            break
        w = np.clip(w + gap * room / tot, lo, hi)
    return w


def max_sharpe(closes, symbols, date, lookback: int = 252, min_obs: int = 60,
               cap_mult: float = 2.0, floor_mult: float = 0.5, shrink: bool = True) -> dict:
    """Long-only max trailing Sharpe: max (w.mu)/sqrt(w'Sw), sum(w)=1, floor/N <= w_i <= cap/N.

    UNLIKE every other scheme here, this one USES EXPECTED RETURNS -- `mu` is the trailing mean. That
    is the whole reason it behaves differently, and the whole reason to distrust it: a trailing mean
    over 252 days is a far noisier estimate than a covariance over the same window, and a ratio
    objective divides by the part that is better estimated. In practice it is a momentum tilt stacked
    on top of a momentum-selected book.

    S is Ledoit-Wolf shrunk (`shrink=True`), for the same reason as `min_variance`: on 20 assets and
    250 observations the sample covariance is poorly conditioned, and a ratio objective is rewarded for
    loading on whichever eigenvalue the sample understated.

    The WEIGHT FLOOR (`floor_mult/N`) exists so the weighting stage cannot silently de-select a name
    the selection stage chose. With bounds of (0, 1) an optimiser will drive names to zero, which is a
    selection decision being taken in the wrong place.

    Falls back to `inverse_vol` -- never to silence -- if the solver fails or the matrix is unusable.
    """
    syms = list(symbols)
    if not syms:
        return {}
    if len(syms) == 1:
        return {syms[0]: 1.0}
    rets = _returns(closes, syms, date, lookback)
    have, short = _split_by_history(rets, syms, min_obs)
    if len(have) < 2:
        return inverse_vol(closes, syms, date, lookback, min_obs)

    r = rets[have].dropna(how="any")
    if len(r) < min_obs:
        r = rets[have].fillna(0.0)
    cov = _covariance(r, shrink)
    if cov is None:
        return inverse_vol(closes, syms, date, lookback, min_obs)
    mu = r.mean().to_numpy(dtype=float)
    if not np.all(np.isfinite(mu)):
        return inverse_vol(closes, syms, date, lookback, min_obs)

    n = len(syms)
    eq = 1.0 / n
    budget = 1.0 - eq * len(short)                     # short-history names take equal weight
    k = len(have)
    # Bounds are expressed against the FULL n, then rescaled onto the sub-budget the optimised names
    # share -- so a short-history name sitting at 1/N does not shift everyone else's bounds.
    lo = (floor_mult / n) / max(budget, 1e-9)
    hi = min(1.0, (cap_mult / n) / max(budget, 1e-9))
    if lo * k > 1.0:                                   # floor infeasible for this many names
        lo = 1.0 / k
    if hi * k < 1.0:                                   # ceiling infeasible
        hi = 1.0 / k
    lo = min(lo, 1.0 / k)
    hi = max(hi, 1.0 / k)

    w = _solve_max_sharpe(mu, cov, lo, hi)
    if w is None:
        return inverse_vol(closes, syms, date, lookback, min_obs)

    out = {s: float(w[i] * budget) for i, s in enumerate(have)}
    out.update({s: eq for s in short})
    return _normalise(out)


def _solve_max_sharpe(mu: np.ndarray, cov: np.ndarray, lo: float, hi: float):
    """SLSQP on -(w.mu)/sqrt(w'Sw). Returns None rather than a bad answer.

    The starting point is equal weight, so a solver that makes no progress degrades to the control
    instead of to something arbitrary -- the same discipline as `_solve_min_var`.
    """
    n = cov.shape[0]
    try:
        from scipy.optimize import minimize
    except ImportError:
        return None
    w0 = np.full(n, 1.0 / n)

    def neg(w):
        v = float(w @ cov @ w)
        return -float(w @ mu) / np.sqrt(v) if v > 1e-18 else 0.0

    try:
        res = minimize(neg, w0, method="SLSQP", bounds=[(lo, hi)] * n,
                       constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0,
                                     "jac": lambda w: np.ones_like(w)}],
                       options={"maxiter": 300, "ftol": 1e-12})
    except Exception:                                  # noqa: BLE001
        return None
    w = res.x if (res.success and np.all(np.isfinite(res.x))) else w0
    w = _project_bounds(w, lo, hi)
    s = w.sum()
    return w / s if s > 0 else None


def _covariance(r: pd.DataFrame, shrink: bool):
    """Ledoit-Wolf covariance, or the sample covariance when sklearn is unavailable."""
    if r.shape[0] < 5 or r.shape[1] < 2:
        return None
    x = r.to_numpy(dtype=float)
    x = x[np.isfinite(x).all(axis=1)]
    if x.shape[0] < 5:
        return None
    if shrink:
        try:
            from sklearn.covariance import LedoitWolf
            return LedoitWolf(assume_centered=False).fit(x).covariance_
        except Exception:                              # noqa: BLE001 - fall through to the sample cov
            pass
    c = np.cov(x, rowvar=False)
    return c if np.all(np.isfinite(c)) else None


def _solve_min_var(cov: np.ndarray, cap: float):
    """SLSQP on w'Sw with an analytic gradient. Returns None rather than a bad answer.

    The starting point is equal weight, so a solver that makes no progress degrades to the control
    instead of to something arbitrary."""
    n = cov.shape[0]
    cap = float(np.clip(cap, 1.0 / n, 1.0))            # a cap below 1/n has no feasible point
    try:
        from scipy.optimize import minimize
    except ImportError:
        return None
    w0 = np.full(n, 1.0 / n)
    try:
        res = minimize(lambda w: float(w @ cov @ w), w0, jac=lambda w: 2.0 * cov @ w,
                       method="SLSQP", bounds=[(0.0, cap)] * n,
                       constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0,
                                     "jac": lambda w: np.ones_like(w)}],
                       options={"maxiter": 200, "ftol": 1e-12})
    except Exception:                                  # noqa: BLE001
        return None
    if not res.success or not np.all(np.isfinite(res.x)):
        return None
    w = np.clip(res.x, 0.0, cap)
    s = w.sum()
    return w / s if s > 0 else None


def _normalise(w: dict) -> dict:
    tot = sum(w.values())
    return {k: v / tot for k, v in w.items()} if tot > 0 else w


def allocate(scheme: str, closes, symbols, date, budget: float,
             lookback: int = 252, min_obs: int = 60, cap_mult: float = 3.0,
             shrink: bool = True, floor_mult: float = 0.5) -> dict:
    """Weights for `symbols` summing to `budget` (the deployed fraction of NAV).

    `closes` may be None ONLY for "equal". Any other scheme raises without it rather than quietly
    falling back -- a risk model that silently becomes equal weight is the kind of divergence that gets
    discovered months later in a report that was wrong the whole time."""
    syms = list(symbols)
    if not syms or budget <= 0:
        return {}
    s = str(scheme).lower()
    if s not in SCHEMES:
        raise ValueError(f"WEIGHTING must be one of {SCHEMES}, got {scheme!r}")
    if s == "equal":
        return {x: budget / len(syms) for x in syms}
    if closes is None:
        raise ValueError(
            f"WEIGHTING={s!r} needs the price history to estimate risk, but the caller passed "
            f"closes=None. Pass the close panel (rows up to and including the decision date) into "
            f"strategy.target_weights(..., closes=...). Refusing to fall back to equal weight: that "
            f"would silently run a different strategy than the one configured.")
    unit = (inverse_vol(closes, syms, date, lookback, min_obs) if s == "invvol"
            else max_sharpe(closes, syms, date, lookback, min_obs, cap_mult, floor_mult, shrink)
            if s == "sharpe_lw"
            else min_variance(closes, syms, date, lookback, min_obs, cap_mult, shrink))
    return {k: v * budget for k, v in unit.items()}


def describe(scheme: str, weights: dict) -> str:
    """One line for the run log: what the split actually looks like."""
    if not weights:
        return f"  [weights] {scheme}: no names"
    v = np.array(sorted(weights.values(), reverse=True), dtype=float)
    tot = v.sum()
    if tot <= 0:
        return f"  [weights] {scheme}: zero budget"
    p = v / tot
    hhi = float((p ** 2).sum())
    return (f"  [weights] {scheme}: {len(v)} names, top {p[0]*100:.1f}% / min {p[-1]*100:.1f}%, "
            f"effective N {1.0/hhi:.1f} (equal weight would be {len(v)})")
