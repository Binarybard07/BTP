# Context Synchronization & Agent Communication Protocol

This document defines the formal protocol for persistent context sharing between **ChatGPT** (Research, Architecture, Experiment Design, Review) and **Antigravity** (Coding, Testing, Debugging, Execution).

---

## 🔄 Interaction Workflows

### 1. ChatGPT $\to$ Antigravity (Specification & Decision Phase)
When ChatGPT proposes a new strategy design, architectural change, or experiment:
1. **Record Decision**: ChatGPT documents architectural and research choices in `docs/decisions.md` or `docs/research_log.md`.
2. **Update Task Queue**: ChatGPT adds specific actionable task items to `docs/todo.md` under the appropriate priority section.
3. **Define Experiment**: If applicable, ChatGPT initializes an experiment entry in `docs/experiments.md` with status `Proposed`.
4. **Antigravity Action**: Antigravity reads the documentation, verifies consistency with existing decisions, and implements the exact specification.

### 2. Antigravity $\to$ ChatGPT (Implementation & Execution Phase)
After Antigravity completes coding, testing, or backtest execution:
1. **Update Implementation Log**: Antigravity logs files modified, tests executed, test results, and known limitations in `docs/implementation_log.md`.
2. **Update Project State**: Antigravity updates the current snapshot in `docs/project_state.md`.
3. **Update Task Queue**: Antigravity marks completed items in `docs/todo.md`.
4. **Record Results**: Antigravity updates `docs/experiments.md` with empirical metrics, status (`Completed`), and execution logs.

---

## 🏷️ Category Disambiguation Rules

Documentation entries must strictly categorize information under one of four headers:

- **`DECISION`**: The architectural or methodology rule established (e.g. "Chronological bar-by-bar strategy decision logic").
- **`IMPLEMENTATION`**: What Antigravity actually built in code (e.g. "Added `FeatureDatasetWriter` class in `src/equity_strategy/features/writer.py`").
- **`RESULT`**: Concrete empirical output from tests or backtests (e.g. "4/4 pytest modules passing").
- **`INTERPRETATION`**: Analysis of what the result means and recommended next actions (e.g. "Software functionality verified; ready for Nautilus-based strategy integration").

These categories must **never** be mixed or fabricated.
