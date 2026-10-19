"""Generate API_SIGNATURES.md — the exact signature of every public export, from introspection.

WHY THIS FILE EXISTS
    Writing the isolation audit, 24 of 120 calls were wrong on the first attempt *with the source open*
    -- roughly a one-in-five miss rate. The names mislead in specific, repeatable ways:
    `PriceBandModel.infer_band` takes a RETURN SERIES, not a symbol; `TaxEngine.apply_fills` keys on
    `date`/`price`, not `ts`/`px`; `SlippageModel.realised()` takes no arguments; `Dashboard` is a
    DATACLASS whose `title` is a field, so `dash.title(...)` raises "'str' object is not callable".

    A prose README cannot keep 120 signatures accurate. This generates them, so the reference cannot
    drift from the code it documents.

Run:  python -m qbeast_exec.gen_signatures      (writes qbeast_exec/API_SIGNATURES.md)
"""
from __future__ import annotations

import dataclasses
import inspect
import pathlib

# Signatures a reader is likely to guess wrong, and what to do instead. Each was actually got wrong
# while auditing, so this list is observed rather than imagined.
GOTCHAS = {
    "TaxEngine.apply_fills": "keys on `date` / `price` by default, **not** `ts` / `px`. Pass the "
                             "column names if yours differ: `apply_fills(f, date='ts', price='px')`.",
    "TaxEngine.assess_all": "takes **no** date. `assess(date)` is the one that advances the FY "
                            "assessment; `assess_all()` returns the whole table.",
    "ChargesConfig.blended": "a factory that **requires** the blended rate: `blended(0.0020)`.",
    "SlippageModel.capacity": "takes a **list** of symbols and a **list** of portfolio sizes: "
                              "`capacity(['AAA'], date, [1e6], top_n=20)`.",
    "SlippageModel.reference_price": "requires an explicit `fallback` price -- there is no default.",
    "SlippageModel.realised": "takes **no** arguments. It reports the fills the model has already "
                              "priced; it does not price one.",
    "PriceBandModel.infer_band": "takes a **return series**, not a symbol: "
                                 "`infer_band(closes['AAA'].pct_change())`.",
    "PriceBandModel.scan": "takes **no** date -- it scans the whole panel.",
    "PriceBandModel.clamp": "argument order is `(symbol, price, prev_close, date=None)` -- price "
                            "**before** prev_close, and the date last and optional.",
    "StaircaseFlag.summary": "takes **no** date (`table()` and `report()` do).",
    "MarketWideBreaker.stress": "needs the move: `stress(date, pct_move)`.",
    "MarketWideBreaker.reopen_gap": "needs the move: `reopen_gap(date, pct_move, recovery=0.5)`.",
    "Dashboard": "a **dataclass**, not a builder. `Dashboard(nav, benchmark=, tax=, slippage=, "
                 "charges=, trades=, title=, subtitle=, periods_per_year=)`. `title` and the rest are "
                 "**fields** -- `dash.title('x')` raises \"'str' object is not callable\". Only "
                 "`stats()`, `to_html()` and `write(path)` are methods.",
    "reprice_to_open": "pass `mask=filled_qty > 0` for an ORDER book -- without it a rejected order's "
                       "`avg_px` of 0 is stamped with a real open price, inventing a trade.",
    "export_books": "**always pass `opens=`**. Without it both books report the previous session's "
                    "close -- a price that was never executable.",
    "replay_fills": "**always pass `opens=`**, for the same reason. `fills` is a sequence of "
                    "`dict(ts=, symbol=, qty=, px=, comm=)`.",
    "make_bars": "`offset_ns=0` for the CLOCK instrument and `offset_ns=1` for tradables. Equal "
                 "offsets make the fill price depend on feed order, i.e. undefined.",
    "DeferredSubmit.queue": "`queue(day, {})` is legitimate and means **flatten to cash**. "
                            "`queue(day, None)` raises -- that is a programming error.",
}


def _sig(obj) -> str:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "(...)"


def main() -> int:
    import qbeast_exec as q

    out = [
        "# qbeast_exec — API signatures",
        "",
        f"**Version {q.__version__}** · {len(q.__all__)} exports · generated from the source by "
        "`python -m qbeast_exec.gen_signatures`.",
        "",
        "> Generated, not written. If this disagrees with the code, the code is right and this file "
        "is stale — regenerate it.",
        "",
        "## Signatures that mislead",
        "",
        "These were each got wrong while writing the isolation audit, **with the source open**. Read "
        "this section before the table.",
        "",
        "| Member | What to watch |",
        "| --- | --- |",
    ]
    for k in sorted(GOTCHAS):
        out.append(f"| `{k}` | {GOTCHAS[k]} |")

    out += ["", "## Every public export", ""]

    funcs, classes, data = [], [], []
    for name in sorted(q.__all__):
        if name.startswith("__"):
            continue
        obj = getattr(q, name)
        if inspect.isclass(obj):
            classes.append((name, obj))
        elif callable(obj):
            funcs.append((name, obj))
        else:
            data.append((name, obj))

    out += ["### Functions", "", "| Function | Signature |", "| --- | --- |"]
    for name, obj in funcs:
        out.append(f"| `{name}` | `{name}{_sig(obj)}` |")

    out += ["", "### Classes", ""]
    for name, obj in classes:
        is_dc = dataclasses.is_dataclass(obj)
        out.append(f"#### `{name}`" + ("  *(dataclass — these are FIELDS, not methods)*" if is_dc
                                       else ""))
        out.append("")
        out.append(f"```python\n{name}{_sig(obj)}\n```")
        out.append("")
        if is_dc:
            flds = [f.name for f in dataclasses.fields(obj)]
            out.append(f"Fields: {', '.join(f'`{f}`' for f in flds)}")
            out.append("")
        methods = [(m, getattr(obj, m)) for m in sorted(dir(obj))
                   if not m.startswith("_") and callable(getattr(obj, m, None))
                   and not isinstance(getattr(obj, m, None), property)]
        props = [m for m in sorted(dir(obj))
                 if not m.startswith("_") and isinstance(getattr(obj, m, None), property)]
        if methods:
            out += ["| Method | Signature |", "| --- | --- |"]
            for m, fn in methods:
                out.append(f"| `{m}` | `{m}{_sig(fn)}` |")
            out.append("")
        if props:
            out.append(f"Properties: {', '.join(f'`{p}`' for p in props)}")
            out.append("")

    if data:
        out += ["### Data", "", "| Name | Type |", "| --- | --- |"]
        for name, obj in data:
            out.append(f"| `{name}` | `{type(obj).__name__}` |")

    out += ["", "---", "",
            "## Verifying a drop-in",
            "",
            "After copying `qbeast_exec/` into your project:",
            "",
            "```bash",
            "python -m qbeast_exec.selftest          # 190 assertions on the accounting itself",
            "python -m qbeast_exec.isolation_audit   # every public callable, from YOUR project",
            "```",
            "",
            "The audit asserts two things the selftest cannot: that **no** module of the originating "
            "project is reachable, and that **every** public callable runs from your copy alone. A "
            "library that quietly imports someone's `config.py` works perfectly at home and fails on "
            "the first machine it is copied to.",
            ""]

    path = pathlib.Path(__file__).resolve().parent / "API_SIGNATURES.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"-> {path}  ({len(out)} lines, {len(funcs)} functions, {len(classes)} classes, "
          f"{len(GOTCHAS)} gotchas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
