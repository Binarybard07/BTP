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

def test_docs_files_exist():
    docs_dir = pathlib.Path(__file__).resolve().parent.parent / "docs"
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
        assert "V1 implementation complete" in content

def test_endpoint_decisions():
    req = urllib.request.Request(f"{BASE_URL}/decisions")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        content = resp.read().decode("utf-8")
        assert "# Architectural & Research Decisions" in content
        assert "Alpha Generation" in content

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
        assert "VAL-001" in content

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

def test_security_blocks_path_traversal():
    """Verify path traversal requests return 403 or 404 and block source/pyproject access."""
    forbidden_urls = [
        f"{BASE_URL}/../pyproject.toml",
        f"{BASE_URL}/..%2fpyproject.toml",
        f"{BASE_URL}/src/equity_strategy/data/loader.py",
        f"{BASE_URL}/.env",
        f"{BASE_URL}/data/sample/synthetic_prices_2021_2024.csv",
    ]

    for url in forbidden_urls:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req) as resp:
                pytest.fail(f"URL '{url}' should have been blocked, returned status {resp.status}")
        except urllib.error.HTTPError as err:
            assert err.code in (403, 404), f"URL '{url}' returned unexpected code {err.code}"

def test_security_blocks_write_methods():
    """Verify POST, PUT, DELETE requests return 405 Method Not Allowed."""
    for method in ["POST", "PUT", "DELETE", "PATCH"]:
        req = urllib.request.Request(f"{BASE_URL}/state", method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                pytest.fail(f"Method '{method}' should be rejected.")
        except urllib.error.HTTPError as err:
            assert err.code == 405
