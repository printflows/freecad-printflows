# MIT License. Copyright (c) 2026 PrintFlows.
"""Local stand-in for the PrintFlows handoff API, for tests and manual runs.

    python tools/mock_server.py [--port 0] [--force 426|429|500] [--store DIR]

Serves on 127.0.0.1 only. Prints the base URL on its first line, then a log
line per request. Point an add-in at it with PRINTFLOWS_BASE_URL=<base URL>.

Endpoints:
  POST /api/handoff               contract validation, 201 or a JSON error
  PUT  /__upload/<token>          checks Content-Type and Content-Length, stores the bytes
  GET  /<en|fr>/<start|commencer>?handoff=<token>
                                  a small page saying what arrived
  POST /__mock__/force            {"status": 426|429|500|503|null, "plain": true|false, "upload": 500|null}
                                  ("plain" sends a 5xx without a JSON body)
  GET  /__mock__/state            JSON: handoffs, uploads, log
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BYTES = 100 * 1024 * 1024
ACCEPTED = [".stl", ".3mf", ".step", ".stp", ".obj"]
CONTENT_TYPES = {
    ".stl": "model/stl",
    ".3mf": "model/3mf",
    ".step": "model/step",
    ".stp": "model/step",
    ".obj": "model/obj",
}
START_PATHS = {"en": "start", "fr": "commencer"}
TTL_SECONDS = 24 * 60 * 60  # handoffs expire after 24 hours


class MockState:
    def __init__(self, store_dir: str):
        self.store_dir = store_dir
        self.lock = threading.Lock()
        self.handoffs: dict[str, dict] = {}
        self.log: list[dict] = []
        self.force_status: int | None = None  # 426, 429 or 500 for POST /api/handoff
        self.force_upload_status: int | None = None  # e.g. 500 for the PUT
        self.force_plain = False  # forced 5xx sent as HTML, no JSON body
        self.reset_next_posts = 0  # drop this many POST connections without answering
        self.delay_seconds = 0.0  # sleep before answering POST /api/handoff
        self.quiet = False

    def record(self, entry: dict) -> None:
        entry["t"] = round(time.time(), 3)
        with self.lock:
            self.log.append(entry)
        if not self.quiet:
            sys.stderr.write("[mock] %s\n" % json.dumps(entry, sort_keys=True))
            sys.stderr.flush()


class Handler(BaseHTTPRequestHandler):
    server_version = "PrintFlowsMock/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> MockState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(
        self, fmt, *args
    ):  # quiet the default access log; MockState.record logs instead
        pass

    # -- helpers ----------------------------------------------------------

    def _base(self) -> str:
        return self.server.base_url  # type: ignore[attr-defined]

    def _send_json(
        self, status: int, payload: dict, extra_headers: dict | None = None
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_text(
        self, status: int, text: str, content_type: str = "text/plain; charset=utf-8"
    ) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self, limit: int) -> bytes | None:
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            return None
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            return None
        if length < 0 or length > limit:
            return None
        chunks = []
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    # -- routes -----------------------------------------------------------

    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/handoff":
            return self._handoff()
        if path == "/__mock__/force":
            body = self._read_body(4096) or b"{}"
            data = json.loads(body.decode("utf-8") or "{}")
            self.state.force_status = data.get("status")
            self.state.force_upload_status = data.get("upload")
            self.state.force_plain = bool(data.get("plain"))
            return self._send_json(200, {"ok": True})
        self._send_json(404, {"error": "not_found"})

    def _handoff(self):
        state = self.state
        raw = self._read_body(64 * 1024)
        entry = {
            "route": "POST /api/handoff",
            "headers": [[k, v] for k, v in self.headers.items()],
            "userAgent": self.headers.get("User-Agent"),
            "contentType": self.headers.get("Content-Type"),
        }
        if state.reset_next_posts > 0:
            state.reset_next_posts -= 1
            entry["result"] = "connection dropped (test)"
            state.record(entry)
            self.close_connection = True
            return
        if state.delay_seconds:
            time.sleep(state.delay_seconds)
        try:
            data = json.loads((raw or b"").decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            data = None
        entry["body"] = data
        status, payload, headers = self._validate(data)
        entry["status"] = status
        entry["response"] = payload
        state.record(entry)
        if payload is None:
            self._send_text(
                status, "<html><body>Service Unavailable</body></html>", "text/html"
            )
        else:
            self._send_json(status, payload, headers)

    def _validate(self, data):
        state = self.state
        if state.force_status == 426:
            return (
                426,
                {
                    "error": "update_required",
                    "message": "This add-in version is no longer supported.",
                    "downloadUrl": "https://printflows.org/en/cad",
                },
                None,
            )
        if state.force_status == 429:
            return (
                429,
                {"error": "rate_limited", "retryAfter": 30},
                {"Retry-After": "30"},
            )
        if state.force_status == 200 and state.force_plain:
            return 200, None, None  # e.g. a captive portal's login page
        if state.force_status and state.force_status >= 500 and state.force_plain:
            return int(state.force_status), None, None  # e.g. a proxy's HTML error page
        if state.force_status == 503:
            return 503, {"error": "busy"}, None  # daily cap reached
        if state.force_status and state.force_status >= 500:
            return int(state.force_status), {"error": "server_error"}, None
        if (self.headers.get("Content-Type") or "").split(";")[
            0
        ].strip().lower() != "application/json":
            return 400, {"error": "invalid", "field": "contentType"}, None
        if not isinstance(data, dict):
            return 400, {"error": "invalid", "field": "body"}, None
        name = data.get("fileName")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 255
            or "/" in name
            or "\\" in name
        ):
            return 400, {"error": "invalid", "field": "fileName"}, None
        size = data.get("sizeBytes")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            return 400, {"error": "invalid", "field": "sizeBytes"}, None
        source = data.get("source")
        if not isinstance(source, str) or not source or len(source) > 32:
            return 400, {"error": "invalid", "field": "source"}, None
        version = data.get("sourceVersion")
        if not isinstance(version, str) or not version or len(version) > 32:
            return 400, {"error": "invalid", "field": "sourceVersion"}, None
        locale = data.get("locale")
        if locale not in ("en", "fr"):
            return 400, {"error": "invalid", "field": "locale"}, None
        ext = os.path.splitext(name)[1].lower()
        if ext not in ACCEPTED:
            return 415, {"error": "unsupported_format", "accepted": ACCEPTED}, None
        if size > MAX_BYTES:
            return 413, {"error": "too_large", "maxBytes": MAX_BYTES}, None
        token = secrets.token_urlsafe(18)
        expires = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(
            seconds=TTL_SECONDS
        )
        record = {
            "token": token,
            "fileName": name,
            "sizeBytes": size,
            "contentType": CONTENT_TYPES[ext],
            "locale": locale,
            "source": source,
            "sourceVersion": version,
            "expires": expires.timestamp(),
            "uploaded": False,
        }
        with state.lock:
            state.handoffs[token] = record
        base = self._base()
        return (
            201,
            {
                "uploadUrl": "%s/__upload/%s" % (base, token),
                "uploadMethod": "PUT",
                "uploadHeaders": {
                    "Content-Type": CONTENT_TYPES[ext],
                    "Content-Length": str(size),
                },
                "openUrl": "%s/%s/%s?handoff=%s"
                % (base, locale, START_PATHS[locale], token),
                "expiresAt": expires.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            },
            None,
        )

    def do_PUT(self):  # noqa: N802
        state = self.state
        path = urllib.parse.urlsplit(self.path).path
        entry = {
            "route": "PUT /__upload",
            "headers": [[k, v] for k, v in self.headers.items()],
            "userAgent": self.headers.get("User-Agent"),
            "contentType": self.headers.get("Content-Type"),
            "contentLength": self.headers.get("Content-Length"),
            "transferEncoding": self.headers.get("Transfer-Encoding"),
        }
        if not path.startswith("/__upload/"):
            entry["status"] = 404
            state.record(entry)
            return self._send_text(404, "NoSuchKey")
        token = path[len("/__upload/") :]
        with state.lock:
            record = state.handoffs.get(token)
        problem = None
        if record is None:
            status, problem = 404, "NoSuchUpload"
        elif record["uploaded"]:
            status, problem = 403, "AlreadyUsed"
        elif time.time() > record["expires"]:
            status, problem = 403, "Expired"
        elif entry["transferEncoding"]:
            status, problem = 411, "MissingContentLength"
        elif entry["contentType"] != record["contentType"]:
            status, problem = 403, "SignatureDoesNotMatch (Content-Type)"
        elif entry["contentLength"] != str(record["sizeBytes"]):
            status, problem = 403, "SignatureDoesNotMatch (Content-Length)"
        else:
            status = 200
        if problem is not None:
            entry["status"] = status
            entry["problem"] = problem
            state.record(entry)
            self.close_connection = True
            return self._send_text(status, problem)
        body = self._read_body(MAX_BYTES)
        if body is None or len(body) != record["sizeBytes"]:
            entry["status"] = 400
            entry["problem"] = "IncompleteBody"
            state.record(entry)
            self.close_connection = True
            return self._send_text(400, "IncompleteBody")
        if state.force_upload_status:
            entry["status"] = int(state.force_upload_status)
            entry["problem"] = "forced"
            state.record(entry)
            return self._send_text(int(state.force_upload_status), "InternalError")
        folder = os.path.join(state.store_dir, token)
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, os.path.basename(record["fileName"]))
        with open(target, "wb") as fh:
            fh.write(body)
        digest = hashlib.sha256(body).hexdigest()
        with state.lock:
            record["uploaded"] = True
            record["storedPath"] = target
            record["sha256"] = digest
        entry.update(
            {"status": 200, "storedPath": target, "sha256": digest, "bytes": len(body)}
        )
        state.record(entry)
        self.send_response(200)
        self.send_header("ETag", '"%s"' % digest[:32])
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        state = self.state
        parts = urllib.parse.urlsplit(self.path)
        if parts.path == "/__mock__/state":
            with state.lock:
                snapshot = {"handoffs": dict(state.handoffs), "log": list(state.log)}
            return self._send_json(200, snapshot)
        segments = [s for s in parts.path.split("/") if s]
        query = urllib.parse.parse_qs(parts.query)
        token = (query.get("handoff") or [""])[0]
        if len(segments) == 2 and START_PATHS.get(segments[0]) == segments[1] and token:
            with state.lock:
                record = state.handoffs.get(token)
            entry = {
                "route": "GET open",
                "path": parts.path,
                "token": token[:6] + "...",
            }
            if record is None:
                entry["status"] = 404
                state.record(entry)
                return self._send_text(404, "Unknown handoff")
            entry["status"] = 200
            entry["uploaded"] = record["uploaded"]
            state.record(entry)
            page = (
                "<!doctype html><meta charset=utf-8><title>PrintFlows mock</title>"
                "<h1>Handoff received</h1><p>File: %s</p><p>Bytes: %d</p><p>Uploaded: %s</p><p>SHA-256: %s</p>"
            ) % (
                html.escape(record["fileName"]),
                record["sizeBytes"],
                record["uploaded"],
                record.get("sha256", "-"),
            )
            return self._send_text(200, page, "text/html; charset=utf-8")
        self._send_text(404, "Not found")


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        port: int = 0,
        store_dir: str | None = None,
        certfile: str | None = None,
        public_host: str = "127.0.0.1",
    ):
        """``certfile`` (cert and key in one PEM) serves https, for TLS tests.

        ``public_host`` is the host name used in the URLs the mock hands out.
        """
        super().__init__(("127.0.0.1", port), Handler)
        self.scheme = "http"
        if certfile:
            import ssl

            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile)
            self.socket = context.wrap_socket(self.socket, server_side=True)
            self.scheme = "https"
        self.public_host = public_host
        self.state = MockState(store_dir or tempfile.mkdtemp(prefix="printflows-mock-"))
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return "%s://%s:%d" % (self.scheme, self.public_host, self.server_address[1])

    def handle_error(self, request, client_address):
        # A client that gave up (timeout test, cancelled upload) is not a server bug.
        if isinstance(sys.exc_info()[1], ConnectionError):
            self.state.record({"route": "connection", "problem": "client went away"})
            return
        super().handle_error(request, client_address)

    def start_background(self) -> "MockServer":
        self._thread = threading.Thread(
            target=self.serve_forever, name="printflows-mock", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument(
        "--force",
        type=int,
        choices=[426, 429, 500, 503],
        help="answer every handoff with this status",
    )
    parser.add_argument(
        "--store", help="folder for uploaded files (default: a new temp folder)"
    )
    args = parser.parse_args(argv)
    server = MockServer(args.port, args.store)
    server.state.force_status = args.force
    print(server.base_url, flush=True)
    sys.stderr.write("[mock] storing uploads in %s\n" % server.state.store_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
