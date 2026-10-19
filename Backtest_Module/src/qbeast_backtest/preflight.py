r"""
qbeast_backtest/preflight.py
-------------------------
ONE JOB: fail immediately, with the command that would have worked, when a script is being run on the
wrong Python.

This machine carries two unrelated interpreters and only one of them can run this project:

    `python` on PATH -> C:\Users\...\AppData\Local\Programs\Python\Python312   (~40 packages)
    the project venv -> E:\QBEAST-AI.N\RegimeSwitch\.venv   (Anaconda-based 3.13, 60+ packages)

On the wrong one nothing fails at import time. It fails LATER and deeper -- "ModuleNotFoundError: nautilus_trader"
raised six frames inside the trend gate -- which reads like a broken repository rather than a wrong shell,
and has already cost real debugging time.

This module is deliberately SEPARATE from config.py and imports nothing but the standard library, so a
standalone script (the Dash viewer, a one-off research file) can guard itself without dragging in the
strategy configuration -- and without inheriting config's own requirements, which it may not share.

    from qbeast_backtest.preflight import require
    require("dash")                      # before the heavy import it protects

config.py re-exports `require`, so `config.require("plotly")` works too and every module that imports
config inherits the configuration-driven check automatically.

Set REGIMESWITCH_SKIP_PREFLIGHT=1 to bypass (e.g. reading constants on a minimal interpreter).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

VENV = Path(__file__).resolve().parent / ".venv" / "Scripts" / "python.exe"

# Why each optional dependency exists, so the error says what is actually broken rather than just naming
# a package the reader has never heard of.
WHY = {
    "nautilus_trader": "needed by EXEC_ENGINE='nautilus' for backtest, paper and live execution",
    "plotly": "needed by the dashboards",
    "dash": "needed by the interactive stock-cycler viewer",
    "sklearn": "scikit-learn",
    "scipy": "scipy",
    "docx": "python-docx, needed to write the .docx reports",
}


def missing(*modules: str) -> list[str]:
    """Which of `modules` are absent. Checked by spec, not imported, so this is cheap enough for
    import time and cannot trigger a heavy package's own side effects."""
    import importlib.util

    out = []
    for m in modules:
        try:
            if importlib.util.find_spec(m) is None:
                out.append(m)
        except (ImportError, ValueError):        # a broken/partial install also counts as missing
            out.append(m)
    return out


def require(*modules: str, why: str = "") -> None:
    """Raise SystemExit naming the missing modules and the interpreter that would have them."""
    if os.environ.get("REGIMESWITCH_SKIP_PREFLIGHT"):
        return
    gone = missing(*modules)
    if not gone:
        return
    lines = [f"RegimeSwitch: wrong Python interpreter{f' - {why}' if why else ''}",
             f"  running: {sys.executable}"]
    lines += [f"  missing: {m}  - {WHY.get(m, 'required')}" for m in gone]
    if VENV.exists() and Path(sys.executable).resolve() != VENV.resolve():
        # sys.argv[0] is "-c" for `python -c ...`, "" in the REPL: no runnable command to echo back
        head = sys.argv[0] if sys.argv and sys.argv[0] not in ("-c", "-m", "") else None
        cmd = (" " + " ".join(f'"{a}"' if " " in a else a for a in sys.argv)) if head else ""
        lines += ["", "  use the project venv:", f'    "{VENV}"{cmd}']
    else:
        lines += ["", f'  install it:  "{sys.executable}" -m pip install ' + " ".join(gone)]
    raise SystemExit("\n".join(lines))
