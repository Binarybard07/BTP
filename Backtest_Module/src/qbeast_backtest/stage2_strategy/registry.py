r"""
stage2_strategy/registry.py
===========================
name -> strategy class. The ONE file a new strategy touches outside its own module.

A registry rather than dynamic discovery: a strategy that appears in a run because a file happened to
be on disk is a strategy nobody chose. Adding a line here is the act of choosing it.

THIS BUILD SHIPS NO STRATEGY. The reference implementation was removed before handoff, so the registry
starts empty and `DEFAULT` is None - a run must name one with `--strategy`. That is deliberate: an
empty registry that refuses is honest, where a silent fallback to a leftover class would produce a
backtest of something nobody asked for and report it under the name that was requested.

Add yours:

    from .my_strategy import MyStrategy

    REGISTRY = {MyStrategy.name: MyStrategy}
    DEFAULT = MyStrategy.name          # optional - lets you omit --strategy
"""
from __future__ import annotations

from .xgboost_regressor_strategy import XGBoostRegressorStrategy

REGISTRY: dict = {
    XGBoostRegressorStrategy.name.lower(): XGBoostRegressorStrategy,
}

DEFAULT = XGBoostRegressorStrategy.name


def available() -> list[str]:
    return sorted(REGISTRY)


def get(name: str | None = None, **kwargs):
    """Instantiate a registered strategy. Raises with the options rather than falling back silently -
    a typo must not quietly run the default and be reported under the name that was asked for."""
    key = str(name or DEFAULT or "").strip().lower()
    if not REGISTRY:
        raise KeyError(
            "no strategies are registered. This build ships the FRAMEWORK only - stages 1 and 3, the "
            "contracts, costs, slippage, tax and both execution engines - and no strategy. Write one "
            "in stage2_strategy/<name>.py against contracts.StrategyOutput, add it to REGISTRY here, "
            "and pass --strategy <name>. See HANDOFF.md.")
    if key not in REGISTRY:
        raise KeyError(f"unknown strategy {name!r}. Registered: {', '.join(available())}. "
                       f"Add yours to stage2_strategy/registry.py.")
    return REGISTRY[key](**kwargs)
