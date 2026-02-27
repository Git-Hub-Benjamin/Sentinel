"""
Sentinel HTTP API — exposes capacity info and proxies inference to Ollama.
Lets OpenClaw (and anything else) treat this machine as a generic compute node.

Endpoints:
  GET  /capacity     → GPU VRAM stats, loaded models, whether accepting requests
  GET  /status       → full sentinel daemon state
  GET  /v1/*         → proxy to Ollama (gated by sentinel state)
  POST /v1/*         → proxy to Ollama (gated by sentinel state)
"""

import json
import logging
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .daemon import SentinelDaemon

log = logging.getLogger("sentinel.api")

OLLAMA_BASE = "http://localhost:11434"


def get_gpu_info() -> dict:
    """Current VRAM stats from nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.total,memory.free,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        parts = [p.strip() for p in result.stdout.strip().split(",")]
        return {
            "name": parts[0],
            "total_vram_mb": int(parts[1]),
            "free_vram_mb": int(parts[2]),
            "used_vram_mb": int(parts[3]),
        }
    except Exception:
        return {"name": "unknown", "total_vram_mb": 0, "free_vram_mb": 0, "used_vram_mb": 0}


def get_loaded_models() -> list:
    """Models currently loaded in Ollama VRAM via /api/ps."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/ps", timeout=5) as resp:
            data = json.loads(resp.read())
        return [
            {
                "name": m["name"],
                "vram_mb": m.get("size_vram", 0) // (1024 * 1024),
            }
            for m in data.get("models", [])
        ]
    except Exception:
        return []


class SentinelHandler(BaseHTTPRequestHandler):
    daemon: "SentinelDaemon" = None  # injected at server start

    def log_message(self, fmt, *args):
        log.debug(f"{self.address_string()} {fmt % args}")

    def send_json(self, data: dict, code: int = 200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _accepting(self) -> tuple[bool, str]:
        """Returns (accepting, reason). Reason is empty string if accepting."""
        status = self.daemon.get_status()
        if status["state"] != "idle":
            return False, f"sentinel state is {status['state']}"
        if not status["inference_running"]:
            return False, "ollama is not running"
        return True, ""

    # ------------------------------------------------------------------ #
    # Routes
    # ------------------------------------------------------------------ #

    def do_GET(self):
        if self.path == "/capacity":
            self._handle_capacity()
        elif self.path == "/status":
            self.send_json(self.daemon.get_status())
        elif self.path.startswith("/v1/"):
            self._proxy()
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.startswith("/v1/"):
            self._proxy()
        else:
            self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        if self.path.startswith("/v1/"):
            self._proxy()
        else:
            self.send_json({"error": "not found"}, 404)

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #

    def _handle_capacity(self):
        gpu = get_gpu_info()
        models = get_loaded_models()
        status = self.daemon.get_status()
        accepting, reason = self._accepting()
        self.send_json({
            "node": socket.gethostname(),
            "accepting_requests": accepting,
            "unavailable_reason": reason,
            "sentinel_state": status["state"],
            "gpu": gpu,
            "loaded_models": models,
            "ssh_sessions": status.get("ssh_sessions", []),
            "owner_user": status.get("owner_user", ""),
        })

    def _proxy(self):
        """Gate on sentinel state then stream the request through to Ollama."""
        accepting, reason = self._accepting()
        if not accepting:
            self.send_json({
                "error": "inference_unavailable",
                "reason": reason,
                "sentinel_state": self.daemon.get_status()["state"],
            }, 503)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else None

        target = f"{OLLAMA_BASE}{self.path}"
        req = urllib.request.Request(target, data=body, method=self.command)
        for key, val in self.headers.items():
            if key.lower() not in ("host", "content-length"):
                req.add_header(key, val)

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                self.send_response(resp.status)
                for key, val in resp.headers.items():
                    # Let our own layer handle transfer encoding
                    if key.lower() == "transfer-encoding":
                        continue
                    self.send_header(key, val)
                self.end_headers()
                # Stream in chunks so large/streamed responses don't buffer
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
        except urllib.error.URLError as e:
            self.send_json({"error": "ollama_unreachable", "detail": str(e)}, 502)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-stream, normal for cancelled requests


def start_api_server(daemon: "SentinelDaemon", host: str, port: int) -> HTTPServer:
    """Start the HTTP API server in a daemon thread. Returns the server."""
    # Bind daemon into handler class so threads don't share mutable state
    handler = type("BoundHandler", (SentinelHandler,), {"daemon": daemon})
    server = HTTPServer((host, port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log.info(f"Sentinel HTTP API on {host}:{port}  —  /capacity  /status  /v1/*")
    return server
