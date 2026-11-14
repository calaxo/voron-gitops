"""Thin async client for the Moonraker HTTP and websocket (JSON-RPC) APIs."""

from __future__ import annotations

import asyncio
import collections
import itertools
import json
from typing import Any

import httpx


class MoonrakerError(Exception):
    """Error returned by Moonraker / Klipper (e.g. 'Must home axis first')."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class MoonrakerClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        long_timeout: float = 3600.0,
        store_settle_s: float = 0.15,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.long_timeout = long_timeout
        self.store_settle_s = store_settle_s
        headers = {"X-Api-Key": api_key} if api_key else {}
        self._http = httpx.AsyncClient(
            base_url=self.base_url, headers=headers, timeout=timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ core
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        timeout: float | None = None,
        files: Any = None,
        data: Any = None,
        raw: bool = False,
    ) -> Any:
        try:
            resp = await self._http.request(
                method,
                path,
                params=params,
                json=json_body,
                files=files,
                data=data,
                timeout=timeout if timeout is not None else self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise MoonrakerError(f"timeout calling {path}: {exc!r}") from exc
        except httpx.HTTPError as exc:
            raise MoonrakerError(f"cannot reach Moonraker at {self.base_url}: {exc!r}") from exc
        if resp.status_code >= 400:
            msg = resp.text
            try:
                err = resp.json().get("error", {})
                msg = err.get("message", msg) if isinstance(err, dict) else str(err)
            except Exception:
                pass
            raise MoonrakerError(str(msg).strip(), resp.status_code)
        if raw:
            return resp.text
        try:
            payload = resp.json()
        except ValueError:
            return resp.text
        if isinstance(payload, dict) and "result" in payload:
            return payload["result"]
        return payload

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> Any:
        return await self.request("POST", path, **kw)

    # -------------------------------------------------------------- printer
    async def info(self) -> dict:
        return await self.get("/printer/info")

    async def list_objects(self) -> list[str]:
        res = await self.get("/printer/objects/list")
        return list(res.get("objects", []))

    async def query(self, objects: dict[str, list[str] | None]) -> dict:
        res = await self.post("/printer/objects/query", json_body={"objects": objects})
        return res.get("status", {})

    async def gcode(self, script: str, timeout: float | None = None) -> Any:
        """Run a gcode script. Moonraker only answers once Klipper finished
        processing it (moves are queued; add M400 to wait for completion)."""
        return await self.post(
            "/printer/gcode/script",
            json_body={"script": script},
            timeout=timeout if timeout is not None else self.long_timeout,
        )

    async def gcode_store(self, count: int = 100) -> list[dict]:
        res = await self.get("/server/gcode_store", params={"count": count})
        return list(res.get("gcode_store", []))

    async def gcode_capture(
        self, script: str, timeout: float | None = None, count: int = 500
    ) -> list[str]:
        """Run a script and return the console responses it produced.

        Uses the gcode_store as a journal: remember the last entry time before
        the call, then return 'response' entries newer than that. Comparing
        against store timestamps avoids any clock skew with the printer host.
        """
        before = await self.gcode_store(1)
        last_t = before[-1]["time"] if before else 0.0
        await self.gcode(script, timeout=timeout)
        if self.store_settle_s:
            await asyncio.sleep(self.store_settle_s)
        after = await self.gcode_store(count)
        out: list[str] = []
        for entry in after:
            if entry.get("time", 0) > last_t and entry.get("type") == "response":
                out.extend(str(entry.get("message", "")).splitlines())
        return out

    async def emergency_stop(self) -> Any:
        return await self.post("/printer/emergency_stop")

    async def restart(self) -> Any:
        return await self.post("/printer/restart")

    async def firmware_restart(self) -> Any:
        return await self.post("/printer/firmware_restart")

    async def endstops(self) -> dict:
        return await self.get("/printer/query_endstops/status")

    async def print_action(self, action: str, filename: str | None = None) -> Any:
        params = {"filename": filename} if filename else None
        return await self.post(f"/printer/print/{action}", params=params)

    # --------------------------------------------------------------- server
    async def temperature_store(self) -> dict:
        return await self.get("/server/temperature_store", params={"include_monitors": "false"})

    async def list_files(self, root: str = "gcodes") -> list[dict]:
        return await self.get("/server/files/list", params={"root": root})

    async def upload(
        self, filename: str, content: bytes, path: str = "", start: bool = False, root: str = "gcodes"
    ) -> Any:
        data = {"root": root, "print": "true" if start else "false"}
        if path:
            data["path"] = path
        files = {"file": (filename, content, "application/octet-stream")}
        return await self.post("/server/files/upload", files=files, data=data, timeout=self.long_timeout)

    async def tail_text(self, path: str, lines: int = 200) -> list[str]:
        """Stream a (possibly large) text file and keep only the last lines."""
        buf: collections.deque[str] = collections.deque(maxlen=lines)
        try:
            async with self._http.stream("GET", path, timeout=self.long_timeout) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise MoonrakerError(resp.text.strip() or f"HTTP {resp.status_code}", resp.status_code)
                async for line in resp.aiter_lines():
                    buf.append(line)
        except httpx.HTTPError as exc:
            raise MoonrakerError(f"cannot read {path}: {exc!r}") from exc
        return list(buf)

    # ------------------------------------------------------------ websocket
    def ws_url(self) -> str:
        url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{url}/websocket"


class MoonrakerWS:
    """Minimal JSON-RPC websocket session (subscriptions + notifications)."""

    def __init__(self, client: MoonrakerClient):
        self.client = client
        self._ids = itertools.count(1)
        self._ws: Any = None
        self._pending: dict[int, asyncio.Future] = {}
        self.notifications: asyncio.Queue = asyncio.Queue()
        self._reader: asyncio.Task | None = None

    async def __aenter__(self) -> "MoonrakerWS":
        import websockets

        headers = {"X-Api-Key": self.client.api_key} if self.client.api_key else None
        kwargs: dict[str, Any] = {"max_size": 16 * 1024 * 1024}
        if headers:
            kwargs["additional_headers"] = headers
        self._ws = await websockets.connect(self.client.ws_url(), **kwargs)
        self._reader = asyncio.create_task(self._read_loop())
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()

    async def _read_loop(self) -> None:
        async for raw in self._ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            mid = msg.get("id")
            if mid is not None and mid in self._pending:
                fut = self._pending.pop(mid)
                if "error" in msg:
                    fut.set_exception(MoonrakerError(str(msg["error"].get("message", msg["error"]))))
                else:
                    fut.set_result(msg.get("result"))
            elif "method" in msg:
                await self.notifications.put(msg)

    async def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> Any:
        mid = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        await self._ws.send(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}, "id": mid}))
        return await asyncio.wait_for(fut, timeout)

    async def subscribe(self, objects: dict[str, list[str] | None]) -> dict:
        return await self.call("printer.objects.subscribe", {"objects": objects})
