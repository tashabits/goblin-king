"""Connection-drain lifecycle for rolling managed-service replacement."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import WebSocket

from goblin_king.service_websocket_proxy import close_websocket


@dataclass
class _DrainState:
    connections: set[WebSocket] = field(default_factory=set)
    retire: Callable[[], Awaitable[None]] | None = None
    callback_started: bool = False
    timeout_task: asyncio.Task[None] | None = None


class WebSocketDrainRegistry:
    """Track local relay ownership and retire replaced services after bounded drain."""

    def __init__(self, *, drain_timeout_seconds: float) -> None:
        self.drain_timeout_seconds = drain_timeout_seconds
        self._lock = asyncio.Lock()
        self._states: dict[str, _DrainState] = {}

    async def register(self, service_id: str, websocket: WebSocket) -> bool:
        """Register one accepted route unless its service is already draining."""
        async with self._lock:
            state = self._states.setdefault(service_id, _DrainState())
            if state.retire is not None:
                return False
            state.connections.add(websocket)
            return True

    async def unregister(self, service_id: str, websocket: WebSocket) -> None:
        """Release a relay and retire a draining service when its last client leaves."""
        callback: Callable[[], Awaitable[None]] | None = None
        timeout_task: asyncio.Task[None] | None = None
        async with self._lock:
            state = self._states.get(service_id)
            if state is None:
                return
            state.connections.discard(websocket)
            if not state.connections and state.retire is None:
                self._states.pop(service_id, None)
                return
            if not state.connections and state.retire is not None and not state.callback_started:
                state.callback_started = True
                callback = state.retire
                timeout_task = state.timeout_task
        if timeout_task is not None and timeout_task is not asyncio.current_task():
            task_loop = timeout_task.get_loop()
            if task_loop is asyncio.get_running_loop():
                timeout_task.cancel()
                await asyncio.gather(timeout_task, return_exceptions=True)
            elif not task_loop.is_closed():
                task_loop.call_soon_threadsafe(timeout_task.cancel)
        if callback is not None:
            await self._retire(service_id, callback)

    async def drain(
        self,
        service_id: str,
        retire: Callable[[], Awaitable[None]],
    ) -> None:
        """Reject new relays and retire after current clients leave or the timeout expires."""
        run_now = False
        async with self._lock:
            state = self._states.setdefault(service_id, _DrainState())
            if state.retire is not None:
                return
            state.retire = retire
            if state.connections:
                state.timeout_task = asyncio.create_task(self._force_drain(service_id))
            else:
                state.callback_started = True
                run_now = True
        if run_now:
            await self._retire(service_id, retire)

    async def active_count(self, service_id: str) -> int:
        """Return the number of local relay connections for proof and tests."""
        async with self._lock:
            state = self._states.get(service_id)
            return len(state.connections) if state is not None else 0

    async def _force_drain(self, service_id: str) -> None:
        await asyncio.sleep(self.drain_timeout_seconds)
        callback: Callable[[], Awaitable[None]] | None = None
        connections: list[WebSocket] = []
        async with self._lock:
            state = self._states.get(service_id)
            if state is None or state.callback_started or state.retire is None:
                return
            state.callback_started = True
            callback = state.retire
            connections = list(state.connections)
        await asyncio.gather(
            *(
                close_websocket(websocket, 1012, "managed service replaced")
                for websocket in connections
            ),
            return_exceptions=True,
        )
        if callback is not None:
            await self._retire(service_id, callback)

    async def _retire(
        self,
        service_id: str,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        try:
            await callback()
        finally:
            async with self._lock:
                self._states.pop(service_id, None)
