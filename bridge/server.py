import json
import os
import pathlib
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict

# Locate project root directory relative to bridge/server.py
BRIDGE_DIR = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = BRIDGE_DIR.parent
DOCS_DIR = PROJECT_ROOT/ "bridge" / "docs"

# Hardcoded mapping of exposed endpoints to documentation files
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


class ContextBridgeRequestHandler(BaseHTTPRequestHandler):
    """
    Read-only HTTP Request Handler exposing project documentation files.
    Security: Restricts access strictly to hardcoded /docs Markdown files.
    """

    server_version = "BTPContextBridge/1.0"

    def _send_response_headers(self, status_code: int, content_type: str, content_length: int):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def do_GET(self):
        # Normalize path by removing query strings or trailing slashes
        path = self.path.split("?")[0].rstrip("/")
        if not path:
            path = "/context"

        # Security check: Prevent path traversal attacks
        if ".." in self.path or "\\" in self.path or "//" in self.path:
            self._send_error_json(403, "Access denied: Invalid path sequence.")
            return

        if path in ENDPOINT_FILE_MAP:
            filename = ENDPOINT_FILE_MAP[path]
            filepath = DOCS_DIR / filename
            self._serve_markdown_file(filepath)

        elif path == "/context":
            self._serve_combined_context()

        else:
            self._send_error_json(404, f"Endpoint '{self.path}' not found. Available endpoints: /state, /decisions, /research, /implementation, /experiments, /todo, /context")

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


        try:
            body_json = json.dumps(combined, indent=2, ensure_ascii=False)
            body_bytes = body_json.encode("utf-8")
            self._send_response_headers(200, "application/json; charset=utf-8", len(body_bytes))
            self.wfile.write(body_bytes)
        except Exception as e:
            self._send_error_json(500, f"Error serializing combined context: {str(e)}")

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
    print("Read-only endpoints: /state, /decisions, /research, /implementation, /experiments, /todo, /context")
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
