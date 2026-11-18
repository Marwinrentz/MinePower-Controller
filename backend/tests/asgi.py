"""Anfragen in-process gegen die FastAPI-App (ohne Netzwerk, ohne Lifespan)."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from app.main import app


async def request(method: str, path: str, *, token: str | None = None, body: Any = None,
                  params: dict | None = None, headers: dict[str, str] | None = None,
                  client: str = "127.0.0.1") -> tuple[int, dict[str, str], Any]:
    """Liefert (Status, Kopfzeilen, JSON bzw. Text)."""
    payload = json.dumps(body).encode() if body is not None else b""
    head = [(b"host", b"test"), (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode())]
    if token:
        head.append((b"authorization", f"Bearer {token}".encode()))
    for key, value in (headers or {}).items():
        head.append((key.lower().encode(), value.encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": urlencode(params or {}).encode(), "root_path": "", "headers": head,
             "client": (client, 0), "server": ("test", 80)}
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        return {"type": "http.disconnect"}

    out: dict[str, Any] = {"status": 0, "headers": {}, "body": b""}

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            out["body"] += message.get("body", b"")

    await app(scope, receive, send)
    raw: bytes = out["body"]
    data: Any = raw.decode(errors="replace")
    if "json" in out["headers"].get("content-type", "") and raw:
        data = json.loads(raw)
    return out["status"], out["headers"], data
