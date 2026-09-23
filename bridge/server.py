import json
import os
import pathlib
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List, Any
from urllib.parse import urlparse, parse_qs

# Locate project root directory relative to bridge/server.py
BRIDGE_DIR = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = BRIDGE_DIR.parent
DOCS_DIR = BRIDGE_DIR / "docs"

# Hardcoded mapping of exposed documentation endpoints
ENDPOINT_FILE_MAP: Dict[str, str] = {
    "/state": "project_state.md",
    "/decisions": "decisions.md",
    "/research": "research_log.md",
    "/implementation": "implementation_log.md",
    "/experiments": "experiments.md",
    "/todo": "todo.md",
}

KEY_MAP: Dict[str, str] = {
    "/state": "project_state",
    "/decisions": "decisions",
    "/research": "research_log",
    "/implementation": "implementation_log",
    "/experiments": "experiments",
    "/todo": "todo",
}

BLOCKED_DIR_NAMES = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".idea", ".vscode", ".gemini"
}

BLOCKED_FILE_PATTERNS = {
    ".env", ".env.local", ".env.production", ".env.development"
}

BLOCKED_FILE_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".pem", ".key", ".crt", ".pfx", ".asc",
    ".parquet", ".feather", ".h5", ".hdf5", ".db", ".sqlite", ".tar",
    ".gz", ".zip", ".exe", ".dll", ".so", ".dylib", ".bin", ".docx", ".pdf"
}

ALLOWED_TEXT_EXTENSIONS = {
    ".py", ".md", ".toml", ".yaml", ".yml", ".json", ".txt", ".ini", ".cfg", ".sh", ".bat", ".csv"
}

MAX_FILE_SIZE_BYTES = 500 * 1024  # 500 KB limit for reading individual text files
MAX_SEARCH_RESULTS = 50  # Maximum number of search matches returned in response payload


def is_path_safe_and_allowed(target_path: pathlib.Path, is_file_read: bool = False) -> bool:
    """
    Validates that target_path is inside PROJECT_ROOT, does not traverse into blocked directories,
    and is not a blocked or secret file.
    """
    try:
        resolved = target_path.resolve()
        if not resolved.is_relative_to(PROJECT_ROOT):
            return False

        # Inspect all relative path components
        rel_parts = resolved.relative_to(PROJECT_ROOT).parts

        # Block root data directory (data/raw/, data/outputs/, data/sample/, etc.)
        if rel_parts and rel_parts[0].lower() == "data":
            return False

        for part in rel_parts:
            if part.lower() in BLOCKED_DIR_NAMES:
                return False
            if part.startswith(".") and part not in (".", ".."):
                return False

        # File-specific checks
        if resolved.is_file():
            if resolved.name in BLOCKED_FILE_PATTERNS or resolved.name.startswith(".env"):
                return False
            if resolved.suffix.lower() in BLOCKED_FILE_EXTENSIONS:
                return False
            if is_file_read:
                if resolved.suffix.lower() not in ALLOWED_TEXT_EXTENSIONS:
                    return False
                if resolved.stat().st_size > MAX_FILE_SIZE_BYTES:
                    return False

        return True
    except Exception:
        return False


class ContextBridgeRequestHandler(BaseHTTPRequestHandler):
    """
    Read-only HTTP Request Handler exposing project documentation files and repository source code.
    Security: Strictly enforces read-only operations, path containment within PROJECT_ROOT,
    and blocks sensitive directories, secrets, and raw binary/dataset files.
    """

    server_version = "BTPContextBridge/1.1"

    def _send_response_headers(self, status_code: int, content_type: str, content_length: int):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def _send_json_response(self, status_code: int, data: Dict[str, Any]):
        body_json = json.dumps(data, indent=2, ensure_ascii=False)
        body_bytes = body_json.encode("utf-8")
        self._send_response_headers(status_code, "application/json; charset=utf-8", len(body_bytes))
        self.wfile.write(body_bytes)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path.rstrip("/")
        if not path:
            path = "/context"

        query_params = parse_qs(parsed_url.query)

        # Basic security check for raw path string traversal
        if ".." in parsed_url.path or "\\" in parsed_url.path or "//" in parsed_url.path:
            self._send_error_json(403, "Access denied: Invalid path sequence.")
            return

        if path in ENDPOINT_FILE_MAP:
            filename = ENDPOINT_FILE_MAP[path]
            filepath = DOCS_DIR / filename
            self._serve_markdown_file(filepath)

        elif path == "/context":
            self._serve_combined_context()

        elif path == "/tree":
            self._serve_repository_tree()

        elif path == "/file":
            self._serve_repository_file(query_params)

        elif path == "/search":
            self._serve_repository_search(query_params)

        else:
            self._send_error_json(
                404,
                f"Endpoint '{self.path}' not found. Available endpoints: /state, /decisions, /research, /implementation, /experiments, /todo, /context, /tree, /file, /search"
            )

    def do_HEAD(self):
        self.do_GET()

    def _send_unsupported_method(self):
        self._send_error_json(405, f"Method '{self.command}' not allowed. Server is read-only.")

    def do_POST(self):
        self._send_unsupported_method()

    def do_PUT(self):
        self._send_unsupported_method()

    def do_DELETE(self):
        self._send_unsupported_method()

    def do_PATCH(self):
        self._send_unsupported_method()

    def _serve_markdown_file(self, filepath: pathlib.Path):
        if not filepath.exists() or not filepath.is_file():
            self._send_error_json(404, f"Requested documentation file '{filepath.name}' does not exist.")
            return

        try:
            content = filepath.read_text(encoding="utf-8")
            body_bytes = content.encode("utf-8")
            self._send_response_headers(200, "text/markdown; charset=utf-8", len(body_bytes))
            self.wfile.write(body_bytes)
        except Exception as e:
            self._send_error_json(500, f"Error reading file '{filepath.name}': {str(e)}")

    def _serve_combined_context(self):
        combined: Dict[str, str] = {}
        for endpoint, filename in ENDPOINT_FILE_MAP.items():
            filepath = DOCS_DIR / filename
            key = KEY_MAP[endpoint]
            if filepath.exists() and filepath.is_file():
                try:
                    combined[key] = filepath.read_text(encoding="utf-8")
                except Exception as e:
                    combined[key] = f"[Error reading file: {e}]"
            else:
                combined[key] = "[File not found]"

        self._send_json_response(200, combined)

    def _serve_repository_tree(self):
        files_list: List[str] = []
        for root, dirs, files in os.walk(PROJECT_ROOT):
            if pathlib.Path(root).resolve() == PROJECT_ROOT.resolve():
                dirs[:] = [d for d in dirs if d.lower() != "data"]
            # Prune blocked directories in place
            dirs[:] = [
                d for d in dirs
                if d.lower() not in BLOCKED_DIR_NAMES and not (d.startswith(".") and d not in (".", ".."))
            ]
            for file in files:
                file_path = pathlib.Path(root) / file
                if is_path_safe_and_allowed(file_path):
                    rel_path = file_path.relative_to(PROJECT_ROOT).as_posix()
                    files_list.append(rel_path)

        files_list.sort()
        response_data = {
            "root": PROJECT_ROOT.name,
            "total_files": len(files_list),
            "files": files_list
        }
        self._send_json_response(200, response_data)

    def _serve_repository_file(self, query_params: Dict[str, List[str]]):
        path_args = query_params.get("path")
        if not path_args or not path_args[0].strip():
            self._send_error_json(400, "Missing 'path' query parameter.")
            return

        raw_path = path_args[0].strip()

        # Reject path traversal tokens
        if ".." in raw_path or "\\" in raw_path:
            self._send_error_json(403, "Access denied: Path traversal characters forbidden.")
            return

        # Sanitize path
        clean_path = raw_path.lstrip("/")
        target_path = PROJECT_ROOT / clean_path

        # Check existence
        if not target_path.exists() or not target_path.is_file():
            self._send_error_json(404, f"File '{raw_path}' does not exist.")
            return

        # Security check
        if not is_path_safe_and_allowed(target_path, is_file_read=True):
            self._send_error_json(403, f"Access to path '{raw_path}' is forbidden or file type/size is restricted.")
            return

        try:
            content = target_path.read_text(encoding="utf-8")
            body_bytes = content.encode("utf-8")
            self._send_response_headers(200, "text/plain; charset=utf-8", len(body_bytes))
            self.wfile.write(body_bytes)
        except UnicodeDecodeError:
            self._send_error_json(400, f"File '{raw_path}' is binary or contains non-UTF-8 content.")
        except Exception as e:
            self._send_error_json(500, f"Error reading file '{raw_path}': {str(e)}")

    def _serve_repository_search(self, query_params: Dict[str, List[str]]):
        q_args = query_params.get("q")
        if not q_args or not q_args[0].strip():
            self._send_error_json(400, "Missing 'q' query parameter.")
            return

        query_str = q_args[0].strip()
        if len(query_str) < 2:
            self._send_error_json(400, "Query string 'q' must be at least 2 characters long.")
            return

        query_lower = query_str.lower()
        all_matches: List[Dict[str, Any]] = []

        for root, dirs, files in os.walk(PROJECT_ROOT):
            if pathlib.Path(root).resolve() == PROJECT_ROOT.resolve():
                dirs[:] = [d for d in dirs if d.lower() != "data"]
            dirs[:] = [
                d for d in dirs
                if d.lower() not in BLOCKED_DIR_NAMES and not (d.startswith(".") and d not in (".", ".."))
            ]
            for file in files:
                file_path = pathlib.Path(root) / file
                if is_path_safe_and_allowed(file_path, is_file_read=True):
                    try:
                        lines = file_path.read_text(encoding="utf-8").splitlines()
                    except Exception:
                        continue

                    rel_path = file_path.relative_to(PROJECT_ROOT).as_posix()
                    for idx, line in enumerate(lines):
                        if query_lower in line.lower():
                            line_num = idx + 1
                            start_ctx = max(0, idx - 1)
                            end_ctx = min(len(lines), idx + 2)
                            context_lines = lines[start_ctx:end_ctx]

                            all_matches.append({
                                "file": rel_path,
                                "line_number": line_num,
                                "line_content": line.strip(),
                                "context": [c.strip() for c in context_lines]
                            })

        total_matches = len(all_matches)
        truncated = total_matches > MAX_SEARCH_RESULTS
        returned_matches = all_matches[:MAX_SEARCH_RESULTS]
        omitted_matches = total_matches - len(returned_matches)

        response_data = {
            "query": query_str,
            "total_matches": total_matches,
            "returned_matches": len(returned_matches),
            "omitted_matches": omitted_matches,
            "truncated": truncated,
            "matches": returned_matches
        }
        self._send_json_response(200, response_data)

    def _send_error_json(self, status_code: int, message: str):
        error_dict = {"error": message, "status_code": status_code}
        body_bytes = json.dumps(error_dict, indent=2).encode("utf-8")
        self._send_response_headers(status_code, "application/json; charset=utf-8", len(body_bytes))
        self.wfile.write(body_bytes)

    def log_message(self, format, *args):
        # Override to log cleanly to stdout
        sys.stdout.write(f"[{self.log_date_time_string()}] Bridge: {format % args}\n")


def run_server(host: str = "127.0.0.1", port: int = 8000):
    """Starts the local read-only HTTP context bridge server."""
    server_address = (host, port)
    httpd = HTTPServer(server_address, ContextBridgeRequestHandler)
    print("============================================================")
    print(f"BTP Shared Context Bridge Server running on http://{host}:{port}")
    print("Read-only endpoints: /state, /decisions, /research, /implementation, /experiments, /todo, /context, /tree, /file, /search")
    print("============================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down context bridge server...")
        httpd.server_close()


if __name__ == "__main__":
    host = os.environ.get("BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("BRIDGE_PORT", "8000"))
    run_server(host=host, port=port)
