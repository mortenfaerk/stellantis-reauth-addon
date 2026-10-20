"""Minimal Home Assistant client over the Supervisor proxy (REST + websocket)."""

import asyncio
import itertools
import logging
from collections.abc import Callable

import aiohttp

_LOGGER = logging.getLogger(__name__)

CORE_URL = "http://supervisor/core"


class HomeAssistant:
    def __init__(self, session: aiohttp.ClientSession, token: str):
        self._session = session
        self._token = token
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._listeners: dict[int, Callable[[dict], None]] = {}
        self._reader: asyncio.Task | None = None

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def rest(self, method: str, path: str, json: dict | None = None, timeout: float = 60):
        async with self._session.request(
            method, f"{CORE_URL}/api{path}", json=json, headers=self._headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(f"{method} {path} -> {resp.status}: {body}")
            return body

    async def connect(self) -> None:
        self._ws = await self._session.ws_connect(f"{CORE_URL}/websocket", heartbeat=30)
        msg = await self._ws.receive_json()
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"unexpected websocket greeting: {msg}")
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        msg = await self._ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"websocket auth failed: {msg.get('type')}")
        self._reader = asyncio.create_task(self._read())

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()

    @property
    def closed(self) -> asyncio.Task | None:
        """Task that finishes when the websocket connection drops."""
        return self._reader

    async def _read(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = msg.json()
                if data.get("type") == "event" and data["id"] in self._listeners:
                    self._listeners[data["id"]](data["event"])
                elif data["id"] in self._pending:
                    future = self._pending.pop(data["id"])
                    if not future.done():
                        future.set_result(data)
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("websocket closed"))
            self._pending.clear()

    async def command(self, payload: dict):
        msg_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        await self._ws.send_json({"id": msg_id, **payload})
        result = await asyncio.wait_for(future, 60)
        if not result.get("success"):
            raise RuntimeError(f"{payload['type']} failed: {result.get('error')}")
        return result.get("result")

    async def subscribe(self, event_type: str, callback: Callable[[dict], None]) -> Callable:
        msg_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        self._listeners[msg_id] = callback
        await self._ws.send_json({"id": msg_id, "type": "subscribe_events", "event_type": event_type})
        await asyncio.wait_for(future, 60)

        async def unsubscribe():
            self._listeners.pop(msg_id, None)
            try:
                await self.command({"type": "unsubscribe_events", "subscription": msg_id})
            except Exception:
                pass

        return unsubscribe

    async def call_service(self, domain: str, service: str, data: dict) -> None:
        await self.command({"type": "call_service", "domain": domain, "service": service, "service_data": data})

    async def mobile_app_notify_services(self) -> list[str]:
        services = await self.command({"type": "get_services"})
        return sorted(s for s in services.get("notify", {}) if s.startswith("mobile_app_"))

    # Config flows

    async def flows_in_progress(self) -> list[dict]:
        return await self.command({"type": "config_entries/flow/progress"})

    async def config_entries(self, domain: str) -> list[dict]:
        return await self.command({"type": "config_entries/get", "domain": domain})

    async def flow_current(self, flow_id: str) -> dict:
        return await self.rest("GET", f"/config/config_entries/flow/{flow_id}")

    async def flow_step(self, flow_id: str, user_input: dict, timeout: float = 60) -> dict:
        return await self.rest("POST", f"/config/config_entries/flow/{flow_id}", json=user_input, timeout=timeout)

    async def flow_abort(self, flow_id: str) -> None:
        await self.rest("DELETE", f"/config/config_entries/flow/{flow_id}")

    async def start_reconfigure(self, domain: str, entry_id: str) -> dict:
        return await self.rest("POST", "/config/config_entries/flow", json={
            "handler": domain, "show_advanced_options": False, "entry_id": entry_id,
        })
