# BTP Local Read-Only Context Bridge

The **Context Bridge** is a lightweight, zero-dependency HTTP server that exposes project documentation and repository source code to external AI agents (such as ChatGPT) over a secure, read-only API.

---

## 🚀 1. How to Start the Bridge

Start the server using UV:

```bash
uv run python bridge/server.py
```

Optional environment variables:
```bash
# Custom host or port
BRIDGE_HOST=127.0.0.1 BRIDGE_PORT=8080 uv run python bridge/server.py
```

---

## 📍 2. Default Local Address & Port

- **Host**: `127.0.0.1` (Localhost only)
- **Port**: `8000`
- **Base URL**: `http://127.0.0.1:8000`

---

## 🔗 3. Available Read-Only Endpoints

### Documentation Endpoints

| Endpoint | Method | Content-Type | Description |
|---|---|---|---|
| `GET /state` | `GET`, `HEAD` | `text/markdown` | Project state snapshot (`project_state.md`) |
| `GET /decisions` | `GET`, `HEAD` | `text/markdown` | Architectural & research decisions (`decisions.md`) |
| `GET /research` | `GET`, `HEAD` | `text/markdown` | Research log & methodology (`research_log.md`) |
| `GET /implementation` | `GET`, `HEAD` | `text/markdown` | Implementation log (`implementation_log.md`) |
| `GET /experiments` | `GET`, `HEAD` | `text/markdown` | Experiment registry (`experiments.md`) |
| `GET /todo` | `GET`, `HEAD` | `text/markdown` | Task queue (`todo.md`) |
| `GET /context` | `GET`, `HEAD` | `application/json` | Combined JSON object of all documentation files |

### Code Inspection Endpoints

| Endpoint | Method | Content-Type | Description |
|---|---|---|---|
| `GET /tree` | `GET`, `HEAD` | `application/json` | Returns recursive repository file tree (relative paths, excludes blocked dirs) |
| `GET /file?path=<path>` | `GET`, `HEAD` | `text/plain` | Reads and returns text contents of specified relative repository file |
| `GET /search?q=<query>` | `GET`, `HEAD` | `application/json` | Case-insensitive search across allowed text files returning line matches and context |

---

## 🛠️ 4. Code Inspection Endpoint Details & Examples

### `GET /tree`
Returns the file structure of the repository, excluding ignored/blocked directories (`.git`, `.venv`, `data`, `__pycache__`, etc.).

**Example Request**:
`GET http://127.0.0.1:8000/tree`

**Example Response**:
```json
{
  "root": "BTP",
  "total_files": 18,
  "files": [
    "README.md",
    "pyproject.toml",
    "uv.lock",
    "bridge/README.md",
    "bridge/server.py",
    "src/equity_strategy/strategy.py"
  ]
}
```

### `GET /file?path=<relative_path>`
Reads a single repository text file given its relative path.

**Example Request**:
`GET http://127.0.0.1:8000/file?path=src/equity_strategy/strategy.py`

**Response**: Plain text file content (`200 OK`). Returns `404 Not Found` for missing files and `403 Forbidden` for path traversal or blocked paths.

### `GET /search?q=<query>`
Searches allowed text files for matching lines (case-insensitive) with small surrounding context. Returns search results with truncation controls if results exceed max limit (50 matches).

**Example Request**:
`GET http://127.0.0.1:8000/search?q=BaseStrategy`

**Example Response**:
```json
{
  "query": "BaseStrategy",
  "total_matches": 3,
  "returned_matches": 3,
  "omitted_matches": 0,
  "truncated": false,
  "matches": [
    {
      "file": "src/equity_strategy/strategy.py",
      "line_number": 9,
      "line_content": "class BaseStrategy(ABC):",
      "context": [
        "from equity_strategy.features.engine import FeatureEngine",
        "class BaseStrategy(ABC):",
        "    \"\"\""
      ]
    }
  ]
}
```

---

## 🔒 5. Security & Isolation Rules

1. **Strict Path Containment**: All file resolution is anchored to `PROJECT_ROOT` (derived from `bridge/server.py`). Path traversal (`../`, `..\`, `%2f..%2f`) is strictly rejected (`403 Forbidden`).
2. **Blocked Directories**: `.git`, `.venv`, `venv`, `__pycache__`, `.pytest_cache`, `.idea`, `.vscode`, `data`, `.gemini`.
3. **Blocked Files & Secrets**: `.env*`, `.pem`, `.key`, `.crt`, `.pfx`, `.asc`, binary files, raw database files (`.parquet`, `.sqlite`, `.db`).
4. **File Size Limits**: Individual file reads are capped at 500 KB (`MAX_FILE_SIZE_BYTES`).
5. **Search Result Limits**: Search queries return at most 50 matches (`MAX_SEARCH_RESULTS`). If query results exceed 50, `truncated=true` is set and `omitted_matches` reports the count of omitted matches.
6. **Read-Only Server**: `POST`, `PUT`, `DELETE`, and `PATCH` requests return `405 Method Not Allowed`.
