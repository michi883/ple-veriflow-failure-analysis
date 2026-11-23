"""Protocol-v1 WebSocket & Streamable-HTTP MCP bridge (`docs/protocol.md`).

Drop-in compatible with ``google-research/ple/sample_submissions/taskbridge.py``:
supports concurrent WebSocket requests (`_waiting` futures), chunked `write_file`
(`WRITE_CHUNK = 4 MiB`), `mcp_allows()` URL validation, `Session.http(url, ...)`
returning decoded bytes, and `MCPClient.initialize()` (`2025-06-18`) with full
JSON + Server-Sent Events (SSE) framing.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import json
import math
import os
import re
import sys
import time
from typing import Any
import urllib.parse

try:
    from websockets.asyncio.server import serve as _ws_serve
    from websockets.exceptions import ConnectionClosed
except ImportError:  # Allows offline unit/loopback testing when websockets is absent
    _ws_serve = None  # type: ignore[assignment]

    class ConnectionClosed(Exception):  # type: ignore[no-redef]
        """Fallback ConnectionClosed exception when websockets is not installed."""


MAX_FRAME = 16 << 20
MAX_HTTP_BYTES = 10_000_000
MAX_WRITE_BYTES = 10_000_000
MAX_TIMEOUT_SEC = 600
MAX_FINAL_CHARS = 1_000_000
WRITE_CHUNK = 4 << 20
HTTP_METHODS = ("GET", "POST", "DELETE", "PUT", "PATCH", "HEAD", "OPTIONS")


class Cancelled(Exception):
    """Raised when the evaluator cancels the task or closes the WebSocket."""


class TaskError(Exception):
    """Raised when the evaluator returns `ok: false` or a request is invalid."""


class MCPError(RuntimeError):
    """Raised when an MCP server returns an HTTP/JSON-RPC error or malformed reply."""


def _frame(message: dict[str, Any]) -> str:
    frame = json.dumps(message)
    if len(frame) > MAX_FRAME:
        raise TaskError(
            f"{message.get('type')}: frame is {len(frame)} bytes, over {MAX_FRAME >> 20} MiB"
        )
    return frame


def _final(text: str) -> str:
    if len(text) <= MAX_FINAL_CHARS:
        return text
    return text[:MAX_FINAL_CHARS] + "\n[... the rest of this message was cut]"


def _text(what: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise TaskError(f"{what} is empty or not text")
    if "\x00" in value:
        raise TaskError(f"{what} holds a NUL byte")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise TaskError(f"{what} is not valid UTF-8") from None
    return value


def _seconds(value: Any) -> int:
    if value is None:
        return 120
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        raise TaskError("timeout_sec must be a finite number of seconds")
    return min(max(1, int(value)), MAX_TIMEOUT_SEC)


def positive_int(value: Any, default: int, cap: int | None = None) -> int:
    number = 0.0
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif not isinstance(value, bool):
        with contextlib.suppress(TypeError, ValueError, OverflowError):
            number = float(value)
    limit = math.ceil(number) if 0 < number < math.inf else default
    return limit if cap is None else min(limit, cap)


def mcp_allows(url: str, servers: list[dict[str, Any]]) -> bool:
    """Check whether `url` is allowed by the evaluator's MCP server URL rules."""
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        return False
    decoded = urllib.parse.unquote(path)
    if (
        "%2f" in path.lower()
        or "%5c" in path.lower()
        or "\\" in path
        or ";" in decoded
        or {".", ".."} & set(decoded.split("/"))
    ):
        return False
    for server in servers:
        base = str(server.get("url") or "")
        if base and (
            url == base
            or ("?" not in base and url.startswith(base + "?"))
            or url.startswith(base.rstrip("/") + "/")
        ):
            return True
    return False


class Session:
    """Single task session supporting concurrent protocol requests."""

    def __init__(self, ws: Any, task: dict[str, Any]) -> None:
        self.ws = ws
        self.task = task
        self.instruction: str = str(task["instruction"])
        self.workdir: str = str(task.get("workdir") or "/")
        limits, servers = task.get("limits"), task.get("mcp_servers")
        self.limits: dict[str, Any] = limits if isinstance(limits, dict) else {}
        self.mcp_servers: list[dict[str, Any]] = (
            [s for s in servers if isinstance(s, dict)] if isinstance(servers, list) else []
        )
        budget = self.limits.get("agent_timeout_sec")
        self.deadline = (
            time.monotonic() + budget if isinstance(budget, (int, float)) and budget > 0 else None
        )
        self.final_message = ""
        self.cancelled = asyncio.Event()
        self._ids = itertools.count(1)
        self._waiting: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    def time_left(self) -> float | None:
        return None if self.deadline is None else self.deadline - time.monotonic()

    async def send(self, message: dict[str, Any]) -> None:
        await self._send(_frame(message))

    async def _send(self, frame: str) -> None:
        async with self._lock:
            await self.ws.send(frame)

    async def _read(self) -> None:
        try:
            async for frame in self.ws:
                try:
                    msg = json.loads(frame)
                except ValueError:
                    msg = None
                if not isinstance(msg, dict):
                    continue
                rid = msg.get("id")
                if msg.get("type") == "result" and isinstance(rid, str) and rid in self._waiting:
                    fut = self._waiting.pop(rid)
                    if not fut.done():
                        fut.set_result(msg)
                elif msg.get("type") == "cancel":
                    break
        except ConnectionClosed:
            pass
        finally:
            self.cancelled.set()
            for fut in self._waiting.values():
                if not fut.done():
                    fut.set_exception(Cancelled())
            self._waiting.clear()

    async def request(self, kind: str, **fields: Any) -> dict[str, Any]:
        if self.cancelled.is_set():
            raise Cancelled()
        rid = str(next(self._ids))
        message = {"type": kind, "id": rid, **{k: v for k, v in fields.items() if v is not None}}
        frame = _frame(message)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._waiting[rid] = future
        try:
            try:
                await self._send(frame)
            except ConnectionClosed:
                self.cancelled.set()
                if future.done() and not future.cancelled():
                    future.exception()
                raise Cancelled() from None
            return await future
        finally:
            self._waiting.pop(rid, None)

    async def exec(self, command: str, timeout_sec: int = 120) -> dict[str, Any]:
        result = await self.request(
            "exec",
            command=_text("exec: the command", command),
            timeout_sec=_seconds(timeout_sec),
        )
        if not result.get("ok", True):
            raise TaskError(result.get("error"))
        return result

    async def read_result(self, path: str) -> dict[str, Any]:
        return await self.request("read_file", path=_text("read_file: the path", path))

    async def read_file(self, path: str) -> bytes:
        result = await self.read_result(path)
        if not result.get("ok"):
            raise TaskError(result.get("error"))
        if result.get("truncated"):
            raise TaskError(f"{path} exceeds the 10 MB single-read limit; read it in parts")
        return base64.b64decode(result.get("content_b64") or "")

    async def write_file(self, path: str, data: bytes | str, append: bool = False) -> int:
        path = _text("write_file: the path", path)
        if not isinstance(data, (bytes, bytearray, str)):
            raise TaskError("write_file: data must be bytes or str")
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        size = 0
        for start in range(0, max(len(raw), 1), WRITE_CHUNK):
            chunk = raw[start : start + WRITE_CHUNK]
            result = await self.request(
                "write_file",
                path=path,
                append=bool(append) or start > 0,
                content_b64=base64.b64encode(chunk).decode("ascii"),
            )
            if not result.get("ok"):
                raise TaskError(result.get("error"))
            size = int(result.get("size", size))
        return size

    async def http(
        self,
        url: str,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        timeout_sec: int = 120,
    ) -> dict[str, Any]:
        url = _text("http: the url", url)
        if not mcp_allows(url, self.mcp_servers):
            raise TaskError(f"http: {url} is not under any declared task MCP server")
        method_up = str(method or "GET").upper()
        if method_up not in HTTP_METHODS:
            raise TaskError(f"http: method must be one of {', '.join(HTTP_METHODS)}")
        hdrs = headers or {}
        if not isinstance(body, (bytes, bytearray)):
            raise TaskError("http: body must be bytes")
        if len(body) > MAX_HTTP_BYTES:
            raise TaskError(f"http: body exceeds {MAX_HTTP_BYTES} bytes")
        result = await self.request(
            "http",
            url=url,
            method=method_up,
            headers=hdrs,
            timeout_sec=_seconds(timeout_sec),
            body_b64=base64.b64encode(bytes(body)).decode("ascii"),
        )
        if not result.get("ok", True):
            raise TaskError(result.get("error"))
        return {
            "status": result["status"],
            "headers": result.get("headers") or {},
            "body": base64.b64decode(result.get("body_b64") or ""),
            "truncated": bool(result.get("truncated")),
        }

    async def log(self, text: str) -> None:
        with contextlib.suppress(ConnectionClosed):
            await self.send({"type": "log", "text": text[:20_000]})

    async def usage(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> None:
        with contextlib.suppress(ConnectionClosed):
            await self.send(
                {
                    "type": "usage",
                    "model": model,
                    "input_tokens": positive_int(input_tokens, 0),
                    "output_tokens": positive_int(output_tokens, 0),
                    "cache_read_tokens": positive_int(cache_read_tokens, 0),
                }
            )


class MCPClient:
    """Streamable-HTTP MCP client supporting JSON and SSE responses."""

    VERSION = "2025-06-18"

    def __init__(
        self,
        session: Session,
        url: str,
        *,
        client: str = "veriflow",
        timeout_sec: int = 300,
    ) -> None:
        self.session = session
        self.url = url
        self.client = client
        self.timeout_sec = timeout_sec
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._ids = itertools.count(1)

    @staticmethod
    def messages(body: str) -> list[dict[str, Any]]:
        text = body.strip()
        if text.startswith(("{", "[")):
            chunks = [text]
        else:
            chunks = [
                "\n".join(
                    line[5:].lstrip()
                    for line in block.splitlines()
                    if line.startswith("data:")
                )
                for block in re.split(r"\r?\n\r?\n", text)
            ]
        found: list[dict[str, Any]] = []
        for chunk in filter(None, chunks):
            try:
                data = json.loads(chunk)
            except ValueError:
                continue
            found.extend(
                m for m in (data if isinstance(data, list) else [data]) if isinstance(m, dict)
            )
        return found

    async def rpc(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        notify: bool = False,
    ) -> dict[str, Any] | None:
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            **({"params": params} if params is not None else {}),
        }
        if not notify:
            message["id"] = next(self._ids)
        reply = await self.session.http(
            self.url,
            "POST",
            dict(self.headers),
            json.dumps(message).encode("utf-8"),
            timeout_sec=self.timeout_sec,
        )
        if reply.get("truncated"):
            raise MCPError(f"{method}: response exceeded protocol body limit")
        for key, value in reply["headers"].items():
            if key.lower() == "mcp-session-id":
                self.headers["Mcp-Session-Id"] = value
        if notify:
            return None
        body = reply["body"].decode("utf-8", "replace")
        if reply["status"] >= 400:
            raise MCPError(f"{method}: HTTP {reply['status']}: {body[:300]!r}")
        for answer in self.messages(body):
            if answer.get("id") == message["id"]:
                if "error" in answer:
                    raise MCPError(f"{method}: {answer['error']}")
                return answer["result"] if isinstance(answer.get("result"), dict) else {}
        raise MCPError(f"{method}: no JSON-RPC answer in {body[:300]!r}")

    async def initialize(self) -> dict[str, Any]:
        result = await self.rpc(
            "initialize",
            {
                "protocolVersion": self.VERSION,
                "capabilities": {},
                "clientInfo": {"name": self.client, "version": "1.0"},
            },
        )
        assert result is not None
        version = result.get("protocolVersion")
        self.headers["MCP-Protocol-Version"] = (
            version if isinstance(version, str) and version else self.VERSION
        )
        await self.rpc("notifications/initialized", notify=True)
        return result

    connect = initialize

    async def list_tools(self, max_pages: int = 100) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max_pages):
            page = await self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            assert page is not None
            found = page.get("tools") if isinstance(page.get("tools"), list) else []
            tools.extend(
                {
                    **t,
                    "description": str(t.get("description") or ""),
                    "inputSchema": t["inputSchema"] if isinstance(t.get("inputSchema"), dict) else {},
                }
                for t in found
                if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]
            )
            next_cursor = page.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        res = await self.rpc("tools/call", {"name": name, "arguments": arguments})
        return res if isinstance(res, dict) else {}


async def _session(ws: Any, handler: Any) -> None:
    try:
        task = json.loads(await ws.recv())
    except ConnectionClosed:
        return
    except ValueError:
        task = None
    if (
        not isinstance(task, dict)
        or task.get("type") != "task"
        or not isinstance(task.get("instruction"), str)
    ):
        with contextlib.suppress(ConnectionClosed):
            await ws.send(json.dumps({"type": "error", "message": "expected a task message first"}))
        return

    session = Session(ws, task)
    reader = asyncio.create_task(session._read())
    work = asyncio.create_task(handler(session))
    stop = asyncio.create_task(session.cancelled.wait())
    try:
        done, _ = await asyncio.wait({work, stop}, return_when=asyncio.FIRST_COMPLETED)
        exc = work.exception() if work in done and not work.cancelled() else Cancelled()
        if exc is None:
            answer = work.result()
            await session.send(
                {
                    "type": "done",
                    "final_message": _final(
                        answer if isinstance(answer, str) and answer else session.final_message
                    ),
                }
            )
        elif not isinstance(exc, Cancelled):
            print(f"harness error: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            if session.final_message:
                await session.log(f"harness error after summary: {type(exc).__name__}: {exc}"[:2000])
                await session.send({"type": "done", "final_message": _final(session.final_message)})
            else:
                await session.send(
                    {"type": "error", "message": f"{type(exc).__name__}: {exc}"[:2000]}
                )
        else:
            await asyncio.wait({work}, timeout=2)
            if not work.done():
                work.cancel()
                await asyncio.wait({work}, timeout=6)
            answer = (
                work.result()
                if work.done() and not work.cancelled() and work.exception() is None
                else None
            )
            final = answer if isinstance(answer, str) and answer else session.final_message
            await session.send(
                {"type": "done", "final_message": _final(final or "(stopped: time is up)")}
            )
    except ConnectionClosed:
        pass
    finally:
        for task_obj in (work, stop, reader):
            task_obj.cancel()


async def serve_forever(handler: Any, port: int | None = None) -> None:
    if _ws_serve is None:
        raise RuntimeError("websockets>=14.0 is required to run serve_forever()")
    listen_port = int(port or os.environ.get("PORT", 8765))

    async def on_connect(ws: Any) -> None:
        await _session(ws, handler)

    async with _ws_serve(
        on_connect, "0.0.0.0", listen_port, max_size=MAX_FRAME, ping_interval=None
    ):
        print(f"listening on {listen_port}", flush=True)
        await asyncio.Future()


def run(handler: Any, port: int | None = None) -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve_forever(handler, port))
