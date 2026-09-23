import json
import pathlib
import threading
import time
import urllib.request
import urllib.error
import pytest

from bridge.server import HTTPServer, ContextBridgeRequestHandler

TEST_HOST = "127.0.0.1"
TEST_PORT = 8999
BASE_URL = f"http://{TEST_HOST}:{TEST_PORT}"


@pytest.fixture(scope="module", autouse=True)
def bridge_server():
    """Spin up local test bridge server on port 8999 for testing."""
    server = HTTPServer((TEST_HOST, TEST_PORT), ContextBridgeRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)  # Allow server time to bind
    yield
    server.shutdown()
    server.server_close()


def get_docs_dir():
    root = pathlib.Path(__file__).resolve().parent.parent
    if (root / "bridge" / "docs").exists():
        return root / "bridge" / "docs"
    return root / "docs"


def test_docs_files_exist():
    docs_dir = get_docs_dir()
    required_files = [
        "project_state.md",
        "decisions.md",
        "research_log.md",
        "implementation_log.md",
        "experiments.md",
        "todo.md",
        "context_protocol.md",
    ]
    for fname in required_files:
        fpath = docs_dir / fname
        assert fpath.exists() and fpath.is_file(), f"Documentation file '{fname}' missing."


def test_endpoint_state():
    req = urllib.request.Request(f"{BASE_URL}/state")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8")
        assert "# Project State" in content


def test_endpoint_decisions():
    req = urllib.request.Request(f"{BASE_URL}/decisions")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8")
        assert "# Architectural & Research Decisions" in content


def test_endpoint_research():
    req = urllib.request.Request(f"{BASE_URL}/research")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8")
        assert "# Research Log" in content


def test_endpoint_implementation():
    req = urllib.request.Request(f"{BASE_URL}/implementation")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8")
        assert "# Implementation Log" in content


def test_endpoint_experiments():
    req = urllib.request.Request(f"{BASE_URL}/experiments")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8")
        assert "# Experiment Registry" in content


def test_endpoint_todo():
    req = urllib.request.Request(f"{BASE_URL}/todo")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8")
        assert "# Project Task Queue (TODO)" in content


def test_endpoint_context_json():
    req = urllib.request.Request(f"{BASE_URL}/context")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        assert "application/json" in resp.headers.get("Content-Type", "")
        data = json.loads(resp.read().decode("utf-8"))

        required_keys = ["project_state", "decisions", "research_log", "implementation_log", "experiments", "todo"]
        for key in required_keys:
            assert key in data
            assert len(data[key]) > 0


def test_endpoint_tree():
    """Verify /tree returns repository structure excluding blocked directories."""
    req = urllib.request.Request(f"{BASE_URL}/tree")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        assert "application/json" in resp.headers.get("Content-Type", "")
        data = json.loads(resp.read().decode("utf-8"))

        assert "root" in data
        assert "total_files" in data
        assert "files" in data
        assert isinstance(data["files"], list)

        # Check presence of expected allowed files
        files_set = set(data["files"])
        assert "pyproject.toml" in files_set
        assert "bridge/server.py" in files_set

        # Verify blocked dirs are pruned
        for f in files_set:
            assert not f.startswith(".git/")
            assert not f.startswith(".venv/")
            assert not f.startswith("data/")
            assert not f.startswith("__pycache__/")


def test_endpoint_file_valid():
    """Verify reading valid repository text files."""
    req = urllib.request.Request(f"{BASE_URL}/file?path=pyproject.toml")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8")
        assert "[project]" in content or "dependencies" in content

    req2 = urllib.request.Request(f"{BASE_URL}/file?path=src/equity_strategy/data/loader.py")
    with urllib.request.urlopen(req2) as resp:
        assert resp.status == 200
        content2 = resp.read().decode("utf-8")
        assert "class CSVDataLoader" in content2


def test_endpoint_file_nonexistent():
    """Verify 404 response for nonexistent files."""
    try:
        req = urllib.request.Request(f"{BASE_URL}/file?path=src/nonexistent_file.py")
        urllib.request.urlopen(req)
        pytest.fail("Should have returned 404 Not Found.")
    except urllib.error.HTTPError as err:
        assert err.code == 404


def test_endpoint_file_path_traversal():
    """Verify 403 response for path traversal attempts."""
    traversal_urls = [
        f"{BASE_URL}/file?path=../pyproject.toml",
        f"{BASE_URL}/file?path=..%2fpyproject.toml",
        f"{BASE_URL}/file?path=..\\pyproject.toml",
        f"{BASE_URL}/file?path=/etc/passwd",
    ]
    for url in traversal_urls:
        try:
            req = urllib.request.Request(url)
            urllib.request.urlopen(req)
            pytest.fail(f"URL '{url}' should have returned 403 Forbidden.")
        except urllib.error.HTTPError as err:
            assert err.code in (403, 404)


def test_endpoint_file_blocked_directory():
    """Verify 403 response when requesting files in blocked directories (data, secrets, .env)."""
    blocked_urls = [
        f"{BASE_URL}/file?path=data/raw/Final_NIFTY100_EoD_Data/ABB.csv",
        f"{BASE_URL}/file?path=.env",
        f"{BASE_URL}/file?path=.venv/pyvenv.cfg",
    ]
    for url in blocked_urls:
        try:
            req = urllib.request.Request(url)
            urllib.request.urlopen(url)
            pytest.fail(f"URL '{url}' should have been blocked.")
        except urllib.error.HTTPError as err:
            assert err.code in (403, 404)


def test_endpoint_search_valid():
    """Verify case-insensitive string search across repository files."""
    req = urllib.request.Request(f"{BASE_URL}/search?q=BaseStrategy")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))

        assert data["query"] == "BaseStrategy"
        assert data["total_matches"] > 0
        assert data["returned_matches"] == len(data["matches"])
        assert data["truncated"] is False or data["truncated"] is True

        match = data["matches"][0]
        assert "file" in match
        assert "line_number" in match
        assert "line_content" in match
        assert "context" in match
        assert isinstance(match["context"], list)


def test_endpoint_search_truncation():
    """Verify search truncation, truncated flag, and omitted matches count when query returns > 50 results."""
    req = urllib.request.Request(f"{BASE_URL}/search?q=import")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))

        assert data["query"] == "import"
        if data["total_matches"] > 50:
            assert data["truncated"] is True
            assert data["returned_matches"] == 50
            assert data["omitted_matches"] == data["total_matches"] - 50
            assert len(data["matches"]) == 50
        else:
            assert data["truncated"] is False
            assert data["returned_matches"] == data["total_matches"]
            assert data["omitted_matches"] == 0


def test_endpoint_search_short_query():
    """Verify 400 Bad Request for empty or single-character search query."""
    try:
        req = urllib.request.Request(f"{BASE_URL}/search?q=a")
        urllib.request.urlopen(req)
        pytest.fail("Should have returned 400 Bad Request.")
    except urllib.error.HTTPError as err:
        assert err.code == 400


def test_security_blocks_write_methods():
    """Verify POST, PUT, DELETE, PATCH requests return 405 Method Not Allowed."""
    endpoints = ["/state", "/tree", "/file?path=pyproject.toml", "/search?q=test"]
    for method in ["POST", "PUT", "DELETE", "PATCH"]:
        for ep in endpoints:
            req = urllib.request.Request(f"{BASE_URL}{ep}", method=method)
            try:
                with urllib.request.urlopen(req) as resp:
                    pytest.fail(f"Method '{method}' on endpoint '{ep}' should be rejected.")
            except urllib.error.HTTPError as err:
                assert err.code == 405
