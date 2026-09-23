# Antigravity Workspace Rules — BTP Equity Strategy Project

## 🔍 BEFORE IMPLEMENTATION
1. **Read Core Context**:
   - Read `docs/project_state.md` to understand current phase and active next steps.
   - Read `docs/decisions.md` to review established architectural and research boundaries.
   - Read `docs/todo.md` to check target tasks and priority level.
2. **Inspect Codebase**:
   - Inspect relevant source files in `src/equity_strategy/` and `tests/`.
3. **Check Decision Alignment**:
   - Verify whether the requested task conflicts with any established decision in `docs/decisions.md`. If a conflict exists, raise it before mutating code.

---

## 🛠️ DURING IMPLEMENTATION
- **Preserve Architecture**: Do not modify working pipeline components unless explicitly instructed.
- **Maintain Modular Separation**: Keep alpha generation (`SignalModel`), portfolio construction (`PortfolioConstructor`), market data loading (`CSVDataLoader`), execution (`NautilusTrader`), and evaluation (`PerformanceAnalyzer`) decoupled.
- **No Unrequested Complexity**: Do not introduce additional features, factor models, ML models, or optimizers unless requested in `docs/todo.md` or prompt.
- **API Verification**: Always verify installed library API signatures (especially NautilusTrader 2.0.x) before writing code that consumes them.

---

## 📊 AFTER IMPLEMENTATION
1. **Run Test Suite**: Run `uv run pytest -v` to ensure zero regressions.
2. **Update Implementation Log**: Log modified files, added functionality, and test results in `docs/implementation_log.md`.
3. **Update Project State**: Update `docs/project_state.md` snapshot.
4. **Update TODO List**: Mark completed items in `docs/todo.md`.
5. **Record Decisions & Experiments**: Log architectural changes in `docs/decisions.md` and backtest runs in `docs/experiments.md`.
