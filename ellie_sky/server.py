from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from urllib.parse import parse_qs, urlparse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class PendingRequest:
    request_id: str
    message: str
    reply: str | None = None
    error: str | None = None
    submitted_at: float = field(default_factory=time.monotonic)
    claimed_at: float | None = None
    completed_at: float | None = None
    done: threading.Event = field(default_factory=threading.Event)


class BridgeState:
    def __init__(self) -> None:
        self.pending: queue.Queue[PendingRequest] = queue.Queue()
        self.requests: dict[str, PendingRequest] = {}
        self.lock = threading.Lock()

    def submit(self, message: str) -> PendingRequest:
        item = PendingRequest(
            request_id=str(uuid.uuid4()),
            message=message,
        )
        with self.lock:
            self.requests[item.request_id] = item
        self.pending.put(item)
        return item

    def complete(self, request_id: str, reply: str | None, error: str | None) -> bool:
        with self.lock:
            item = self.requests.get(request_id)
        if not item:
            return False
        item.reply = reply
        item.error = error
        item.completed_at = time.monotonic()
        item.done.set()
        return True


class BridgeServer:
    def __init__(self, host: str, port: int, state: BridgeState):
        self.state = state

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status: int, data: dict) -> None:
                body = json.dumps(data).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                origin = self.headers.get("Origin", "")
                if origin in {"http://127.0.0.1:8000", "http://localhost:8000"}:
                    self.send_header("Access-Control-Allow-Origin", origin)
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                origin = self.headers.get("Origin", "")
                if origin in {"http://127.0.0.1:8000", "http://localhost:8000"}:
                    self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self._send(200, {"ok": True})
                    return
                if parsed.path == "/next":
                    query = parse_qs(parsed.query)
                    try:
                        wait_seconds = min(
                            30.0,
                            max(0.0, float(query.get("wait", ["0"])[0])),
                        )
                    except ValueError:
                        wait_seconds = 0.0
                    try:
                        item = outer.state.pending.get(timeout=wait_seconds)
                    except queue.Empty:
                        self._send(200, {"request": None})
                        return
                    item.claimed_at = time.monotonic()
                    self._send(200, {
                        "request": {
                            "id": item.request_id,
                            "message": item.message,
                        }
                    })
                    return
                self._send(404, {"error": "not found"})

            def do_POST(self) -> None:
                if self.path != "/reply":
                    self._send(404, {"error": "not found"})
                    return
                length = int(self.headers.get("Content-Length", "0"))
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    self._send(400, {"error": "invalid json"})
                    return
                ok = outer.state.complete(
                    str(payload.get("id", "")),
                    payload.get("reply"),
                    payload.get("error"),
                )
                self._send(200 if ok else 404, {"ok": ok})

            def log_message(self, format: str, *args: object) -> None:
                return

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
