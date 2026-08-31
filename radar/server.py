"""Opportunity Radar — a self-hosted MCP server (Streamable HTTP).

This is the Alexa+ integration surface: Alexa+ speaks MCP, so anything exposed
here can be asked for out loud. The transport follows MCP spec 2025-11-25 —
one endpoint, POST for JSON-RPC, optional GET for an SSE stream, and a session
id handed out on `initialize`.

Run it:
    python -m radar.server            # http://0.0.0.0:8080/mcp
    PORT=9000 python -m radar.server
"""
from __future__ import annotations

import inspect
import json
import os
import re
import secrets
import typing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .tools import ALL_TOOLS

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "opportunity-radar", "title": "Opportunity Radar", "version": "0.1.0"}

INSTRUCTIONS = (
    "Funding opportunities the caller may be eligible for, read live from grants.gov. "
    "Every count, deadline and award figure in a tool result came from that call — "
    "never state a number these tools did not return, and if a result carries an "
    "`error` field, say the data is unavailable rather than estimating. Deadlines are "
    "in days from today. When speaking results aloud, lead with what closes soonest."
)

_SESSIONS: set[str] = set()

_PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean",
               dict: "object", list: "array"}


def _schema_for(fn) -> dict:
    """Build an input schema from the function signature and type hints."""
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)
    props, required = {}, []
    for name, param in sig.parameters.items():
        ann = hints.get(name, str)
        prop = {"type": _PY_TO_JSON.get(ann, "string")}
        doc = fn.__doc__ or ""
        m = re.search(rf"`{re.escape(name)}`([^.]*\.)", doc)
        if m:
            prop["description"] = re.sub(r"\s+", " ", m.group(0)).strip()
        props[name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(name)
        else:
            prop["default"] = param.default
    return {"type": "object", "properties": props, "required": required}


def _describe(fn) -> dict:
    doc = inspect.getdoc(fn) or ""
    first, _, rest = doc.partition("\n\n")
    return {
        "name": fn.__name__,
        "title": fn.__name__.replace("_", " ").title(),
        "description": (first + ("\n\n" + rest if rest else "")).strip(),
        "inputSchema": _schema_for(fn),
    }


TOOLS = {fn.__name__: fn for fn in ALL_TOOLS}
TOOL_LIST = [_describe(fn) for fn in ALL_TOOLS]


def _result(payload: dict) -> dict:
    """MCP tool result: human-readable text plus the structured object."""
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}],
        "structuredContent": payload,
        "isError": bool(payload.get("error")),
    }


def handle_rpc(msg: dict) -> dict | None:
    """Dispatch one JSON-RPC message. Returns None for notifications."""
    method, mid = msg.get("method"), msg.get("id")
    params = msg.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOL_LIST})
    if method == "tools/call":
        name = params.get("name")
        fn = TOOLS.get(name)
        if fn is None:
            return err(-32602, f"unknown tool: {name}")
        args = params.get("arguments") or {}
        allowed = set(inspect.signature(fn).parameters)
        unknown = set(args) - allowed
        if unknown:
            return err(-32602, f"unexpected arguments: {sorted(unknown)}")
        try:
            return ok(_result(fn(**args)))
        except TypeError as e:
            return err(-32602, f"bad arguments for {name}: {e}")
        except Exception as e:  # noqa: BLE001 - a tool crash must not kill the session
            return ok(_result({"error": type(e).__name__, "detail": str(e),
                               "say": "That lookup failed. I have no numbers for you on it."}))
    return err(-32601, f"method not found: {method}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "opportunity-radar/0.1"

    def log_message(self, fmt, *args):  # quieter console
        print(f"[mcp] {self.address_string()} {fmt % args}")

    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json",
              extra: dict | None = None):
        self.send_response(code)
        if body:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type, mcp-session-id, mcp-protocol-version")
        self.send_header("Access-Control-Expose-Headers", "mcp-session-id")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, extra={"Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS"})

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/healthz"):
            return self._send(200, json.dumps({"ok": True, "tools": len(TOOL_LIST),
                                               "protocol": PROTOCOL_VERSION}).encode())
        if not self.path.startswith("/mcp"):
            return self._send(404, b'{"error":"not found"}')
        # SSE stream: opened by clients that want server-initiated messages.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(b": stream open\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_DELETE(self):
        _SESSIONS.discard(self.headers.get("mcp-session-id", ""))
        self._send(204)

    def do_POST(self):
        if not self.path.startswith("/mcp"):
            return self._send(404, b'{"error":"not found"}')
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            msg = json.loads(raw or b"{}")
        except (ValueError, TypeError):
            return self._send(400, json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "parse error"}}).encode())

        batch = isinstance(msg, list)
        messages = msg if batch else [msg]
        replies = [r for r in (handle_rpc(m) for m in messages) if r is not None]

        headers = {}
        if any(m.get("method") == "initialize" for m in messages):
            sid = secrets.token_hex(16)
            _SESSIONS.add(sid)
            headers["mcp-session-id"] = sid
        headers["mcp-protocol-version"] = PROTOCOL_VERSION

        if not replies:  # notification-only POST
            return self._send(202, extra=headers)
        body = json.dumps(replies if batch else replies[0], ensure_ascii=False).encode()
        self._send(200, body, extra=headers)


def main() -> int:
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"Opportunity Radar MCP on http://{host}:{port}/mcp  ({len(TOOL_LIST)} tools, spec {PROTOCOL_VERSION})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
