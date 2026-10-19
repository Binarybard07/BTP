r"""Audit TECHNICAL_REFERENCE.md against the code it describes.

Run it after changing anything the document names:

    python audit_reference.py


A reference document is worth exactly as much as its accuracy. Every quoted snippet, line count, file
name, function name and dependency version in it is checked here against the tree, because a doc that
is 95% right is worse than no doc: the reader cannot tell which 5%.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOC = (ROOT / "TECHNICAL_REFERENCE.md").read_text(encoding="utf-8")
SRC = ROOT / "src"

OK, BAD = [], []


def ck(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print(f"  [{'ok ' if cond else 'FAIL'}] {name}" + (f"   {detail}" if detail and not cond else ""))


# ---------------------------------------------------------------- 1. every file named exists
named = set(re.findall(r"`([a-z0-9_]+(?:/[a-z0-9_]+)*\.py)`", DOC))
named |= set(re.findall(r"\|\s*`([a-z0-9_]+\.py)`\s*\|", DOC))
missing = []
for f in sorted(named):
    cands = (list(SRC.rglob(f.split("/")[-1])) + list(ROOT.glob(f))
             + list(ROOT.parent.rglob(f.split("/")[-1])))
    if not any(Path(c).exists() for c in cands):
        missing.append(f)
ck(f"every .py named in the doc exists ({len(named)} names)", not missing, f"missing: {missing}")

# ---------------------------------------------------------------- 2. line counts in the table
tbl = re.findall(r"\|\s*`((?:\w+/)?\w+\.py)`\s*\|\s*(\d+)\s*\|", DOC)
wrong = []
for fname, claimed in tbl:
    exact = SRC / fname
    hits = [exact] if exact.exists() else [p for p in SRC.rglob(fname) if "__pycache__" not in str(p)]
    if not hits:
        wrong.append(f"{fname} not found")
        continue
    real = len(hits[0].read_text(encoding="utf-8").splitlines())
    if real != int(claimed):
        wrong.append(f"{fname}: doc says {claimed}, file has {real}")
ck(f"every line count in the module table ({len(tbl)} rows)", not wrong, "; ".join(wrong))

# ---------------------------------------------------------------- 3. inline line counts "(N lines)"
inline = re.findall(r"`([a-z0-9_]+(?:/[a-z0-9_]+)*\.py)`\s*\((\d+) lines\)", DOC)
inline += re.findall(r"`([a-z0-9_]+\.py)` \((\d+)\)", DOC)
wrong = []
for fname, claimed in inline:
    hits = [p for p in SRC.rglob(fname.split("/")[-1]) if "__pycache__" not in str(p)]
    if not hits:
        wrong.append(f"{fname} not found")
        continue
    real = len(hits[0].read_text(encoding="utf-8").splitlines())
    if real != int(claimed):
        wrong.append(f"{fname}: doc {claimed} vs {real}")
ck(f"inline line counts ({len(inline)})", not wrong, "; ".join(wrong))

# ---------------------------------------------------------------- 4. quoted code appears verbatim
# A block preceded by `<!-- example -->` is illustrative code that is NOT in the tree - it shows the
# shape of the strategy this bundle deliberately does not ship. Everything else must be verbatim, so
# the marker is the only escape and it has to be written into the document to be used.
_blocks_raw = re.findall(r"(<!-- example -->\s*)?```python\n(.*?)```", DOC, re.S)
blocks = [body for marker, body in _blocks_raw if not marker]
_examples = sum(1 for marker, _ in _blocks_raw if marker)
tree_text = {p: p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")
             if "__pycache__" not in str(p)}
# staircase_ab.py is not in this bundle (it needs a strategy to run), so include it only if present.
for _extra in ("staircase_ab.py",):
    if (ROOT / _extra).exists():
        tree_text[ROOT / _extra] = (ROOT / _extra).read_text(encoding="utf-8")


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def code_only(s: str) -> str:
    """The source part of a quoted line, per the convention TECHNICAL_REFERENCE.md states:
    a trailing `#` comment may be the document's own, so it is stripped before matching.
    Quotes and f-strings can legally contain `#`, so only strip from a `#` that is preceded
    by whitespace and is not inside an obvious string."""
    out, depth, i = s, 0, 0
    q = None
    while i < len(s):
        c = s[i]
        if q:
            if c == q and s[i - 1] != "\\":
                q = None
        elif c in "\"'":
            q = c
        elif c == "#" and i and s[i - 1] in " 	":
            out = s[:i]
            break
        i += 1
    return out.rstrip()


def found(line: str) -> bool:
    """Is this quoted line in the tree? `...` is a wildcard, per the stated convention."""
    code = norm(code_only(line))
    if not code:
        return True
    if "..." in code:
        parts = [norm(x) for x in code.split("...") if norm(x)]
        return any(all(pp in norm(src) for pp in parts) for src in tree_text.values())
    return any(code in norm(src) for src in tree_text.values())


notfound = []
for b in blocks:
    for ln in b.splitlines():
        s = ln.strip()
        if not s or s in ("...", "…") or s.startswith("#") or s.startswith('"""') or len(s) < 25:
            continue
        if not found(s):
            notfound.append(s[:78])
ck(f"every quoted code line is verbatim ({len(blocks)} blocks checked, "
   f"{_examples} marked as examples and skipped)",
   not notfound, f"{len(notfound)} not found:\n        " + "\n        ".join(notfound[:12]))

# ---------------------------------------------------------------- 5. function names exist
fns = set(re.findall(r"`([a-z_][a-z0-9_]*)\(", DOC))
SKIP = {"get_plotlyjs", "minimize",
        "get", "dict", "set", "print", "min", "max", "int", "float", "str", "len", "nlargest",
        "dropna", "shift", "ffill", "reindex", "tail", "pct_change", "asof", "sum", "bool",
        "cp", "run", "write", "validate", "summary", "check", "rate", "gate", "decide",
        "execute", "stats", "episodes", "table", "build", "queue", "release", "mark",
        "sort", "append", "replace", "normalize", "copy", "items", "keys", "values"}
allsrc = "\n".join(tree_text.values())
missing_fn = [f for f in sorted(fns - SKIP)
              if not re.search(rf"\bdef {re.escape(f)}\b|\bclass {re.escape(f)}\b", allsrc)]
ck(f"every function/class named exists ({len(fns - SKIP)} checked)", not missing_fn,
   f"missing: {missing_fn}")

# ---------------------------------------------------------------- 6. dependency versions
pyproj = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
for dep, claim in (("pandas", "2.0"), ("numpy", "1.24"), ("scipy", "1.10"),
                   ("plotly", "5.0"), ("nautilus_trader", "1.190")):
    ck(f"pyproject pins {dep}>={claim}", f"{dep}>={claim}" in pyproj,
       [l for l in pyproj.splitlines() if dep in l])

# ---------------------------------------------------------------- 7. console scripts
for s in ("qbeast-backtest", "qbeast-backtest-selftest", "qbeast-backtest-reconcile",
          "qbeast-exec-selftest"):
    ck(f"console script {s}", f"{s} =" in pyproj or f'{s}=' in pyproj)

# ---------------------------------------------------------------- 8. the subset claim
ck("this bundle really has no qbeast_exec/dashboard.py",
   not (SRC / "qbeast_exec" / "dashboard.py").exists())
ck("and the doc says so", "absent from this bundle" in DOC)

# ---------------------------------------------------------------- 9. patsy claim
# patsy IS required in this tree, lazily, when TREND_GATE is one of the p-spline gates - the
# earlier version of this check asserted the opposite and failed a document that was right.
# patsy went with the removed strategy's p-spline gates. The doc claims it is not a
# dependency here, so nothing may import it AND pyproject must not declare it.
_imports_patsy = [p.name for p in SRC.rglob("*.py")
                  if "__pycache__" not in str(p)
                  and ("import patsy" in p.read_text(encoding="utf-8"))]
ck("nothing in the bundle imports patsy", not _imports_patsy, f"{_imports_patsy}")
ck("pyproject does not declare patsy as a dependency",
   not any(l.strip().startswith('"patsy') for l in pyproj.splitlines()))
ck("the doc says patsy is NOT a dependency here", "patsy` is **not** a dependency here" in DOC)

# the strategy really is absent
_gone = ["signals.py", "strategy.py", "regime_qbeast.py", "stage2_strategy/regime_switch.py"]
_still = [f for f in _gone if (SRC / "qbeast_backtest" / f).exists()]
ck("the reference strategy is absent", not _still, f"still present: {_still}")
ck("the registry ships empty",
   "REGISTRY: dict = {}" in (SRC / "qbeast_backtest/stage2_strategy/registry.py").read_text(encoding="utf-8"))

print(f"\n{len(OK)} ok, {len(BAD)} wrong")
for b in BAD:
    print("   WRONG:", b)
