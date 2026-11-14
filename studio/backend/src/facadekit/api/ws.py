from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from facadekit.errors import NOT_FOUND, PROJECTOR_NOT_CONNECTED, PROJECTOR_TIMEOUT, FkError

log = logging.getLogger(__name__)

REPLACED_CLOSE_CODE = 4000
MAX_IMAGES = 4
SEND_TIMEOUT_S = 5.0

router = APIRouter()


class ProjectorStatus(BaseModel):
    connected: bool
    width: int | None
    height: int | None
    dpr: float | None
    mode: str  # "blank" | "white" | "grid" | "outlines" | "compositor" | "image"


class EventHub:
    """Broadcast of JSON events to every connected UI socket."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sockets: list[WebSocket] = []
        self._lock = asyncio.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._sockets.append(ws)
        try:
            while True:
                await ws.receive_text()  # server -> client only; ignore anything received
        except WebSocketDisconnect:
            pass
        except Exception:
            log.debug("events socket closed", exc_info=True)
        finally:
            if ws in self._sockets:
                self._sockets.remove(ws)

    def publish(self, message: dict[str, Any]) -> None:
        """Thread-safe: schedules the broadcast on the bound loop (no-op without a loop)."""
        loop = self._loop
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(message), loop)
        except RuntimeError:  # loop closed
            pass

    async def _broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            for ws in list(self._sockets):
                try:
                    await ws.send_json(message)
                except Exception:
                    if ws in self._sockets:
                        self._sockets.remove(ws)


class _Waiter:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.acked = False


class ProjectorHub:
    def __init__(
        self,
        events: EventHub,
        *,
        sim_size: tuple[int, int] | None = None,
        ack_timeout: float = SEND_TIMEOUT_S,
    ) -> None:
        self._events = events
        self._sim_size = sim_size
        self.ack_timeout = ack_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._send_lock = asyncio.Lock()  # serialises ws.send_json on the loop
        self._lock = threading.Lock()  # guards everything below
        self._ws: WebSocket | None = None
        self._size: tuple[int, int] | None = None
        self._dpr: float | None = None
        self._mode = "blank"
        self._seq = 0
        self._waiters: dict[int, _Waiter] = {}
        self._images: OrderedDict[int, bytes] = OrderedDict()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # -- status ------------------------------------------------------------

    def status(self) -> ProjectorStatus:
        with self._lock:
            if self._size is not None:
                return ProjectorStatus(
                    connected=True,
                    width=self._size[0],
                    height=self._size[1],
                    dpr=self._dpr,
                    mode=self._mode,
                )
            if self._sim_size is not None:
                return ProjectorStatus(
                    connected=True,
                    width=self._sim_size[0],
                    height=self._sim_size[1],
                    dpr=1.0,
                    mode=self._mode,
                )
            return ProjectorStatus(
                connected=False, width=None, height=None, dpr=None, mode=self._mode
            )

    def set_sim_size(self, size: tuple[int, int]) -> None:
        """Simulator only: the pretend projector now has this many pixels."""
        with self._lock:
            if self._sim_size is None or self._sim_size == size:
                return
            self._sim_size = size
        self._publish_status()

    def set_mode(self, mode: str) -> None:
        with self._lock:
            changed = mode != self._mode
            self._mode = mode
        if changed:
            self._publish_status()

    def _publish_status(self) -> None:
        self._events.publish({"type": "projector", "status": self.status().model_dump()})

    # -- page connection ---------------------------------------------------

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        with self._lock:
            old = self._ws
            self._ws = ws
            self._size = None
            self._dpr = None
            released = self._release_waiters()
        for waiter in released:
            waiter.event.set()
        if old is not None:
            try:
                await old.close(code=REPLACED_CLOSE_CODE)
            except Exception:
                log.debug("closing replaced projector socket failed", exc_info=True)
        try:
            while True:
                data = await ws.receive_json()
                if isinstance(data, dict):
                    self._handle(data)
        except WebSocketDisconnect:
            pass
        except Exception:
            log.debug("projector socket closed", exc_info=True)
        finally:
            with self._lock:
                current = self._ws is ws
                if current:
                    self._ws = None
                    self._size = None
                    self._dpr = None
                    released = self._release_waiters()
                else:
                    released = []
            for waiter in released:
                waiter.event.set()
            if current:
                self._publish_status()

    def _release_waiters(self) -> list[_Waiter]:
        # Caller holds the lock. Waiters wake up with acked == False.
        waiters = list(self._waiters.values())
        self._waiters.clear()
        return waiters

    def _handle(self, data: dict[str, Any]) -> None:
        kind = data.get("type")
        if kind == "hello":
            try:
                dpr = float(data["dpr"])
                width = round(float(data["width"]) * dpr)
                height = round(float(data["height"]) * dpr)
            except (KeyError, TypeError, ValueError):
                return
            if width <= 0 or height <= 0:
                return
            with self._lock:
                self._size = (width, height)
                self._dpr = dpr
            self._publish_status()
        elif kind == "ack":
            seq = data.get("seq")
            with self._lock:
                waiter = self._waiters.pop(seq, None) if isinstance(seq, int) else None
            if waiter is not None:
                waiter.acked = True
                waiter.event.set()

    # -- sending -----------------------------------------------------------

    def _next_seq(self) -> int:
        # Caller holds the lock.
        self._seq += 1
        return self._seq

    async def _send_json(self, ws: WebSocket, message: dict[str, Any]) -> None:
        async with self._send_lock:
            await ws.send_json(message)

    def send(
        self, message: dict[str, Any], *, wait_ack: bool, timeout: float | None = None
    ) -> None:
        """Thread-safe and blocking. Never call from the event-loop thread with wait_ack=True."""
        timeout = self.ack_timeout if timeout is None else timeout
        loop = self._loop
        with self._lock:
            ws = self._ws
            if ws is None or loop is None:
                if self._sim_size is not None:
                    return
                raise FkError(PROJECTOR_NOT_CONNECTED, "no projector page is connected")
            seq = message.get("seq")
            if not isinstance(seq, int):
                seq = self._next_seq()
            payload = {**message, "seq": seq}
            waiter: _Waiter | None = None
            if wait_ack:
                waiter = _Waiter()
                self._waiters[seq] = waiter
        try:
            future = asyncio.run_coroutine_threadsafe(self._send_json(ws, payload), loop)
            future.result(timeout=timeout)
        except Exception as exc:
            self._drop_waiter(seq)
            raise FkError(PROJECTOR_NOT_CONNECTED, "could not reach the projector page") from exc
        if waiter is None:
            return
        if not waiter.event.wait(timeout):
            self._drop_waiter(seq)
            raise FkError(PROJECTOR_TIMEOUT, "projector page did not acknowledge in time")
        if not waiter.acked:
            raise FkError(PROJECTOR_NOT_CONNECTED, "projector page disconnected")

    def _drop_waiter(self, seq: int) -> None:
        with self._lock:
            self._waiters.pop(seq, None)

    # -- images ------------------------------------------------------------

    def put_image(self, png: bytes) -> int:
        with self._lock:
            seq = self._next_seq()
            self._images[seq] = png
            while len(self._images) > MAX_IMAGES:
                self._images.popitem(last=False)
            return seq

    def get_image(self, seq: int) -> bytes:
        with self._lock:
            png = self._images.get(seq)
        if png is None:
            raise FkError(NOT_FOUND, f"projector image not found: {seq}")
        return png


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await ws.app.state.fk.events.connect(ws)


@router.websocket("/ws/projector")
async def ws_projector(ws: WebSocket) -> None:
    await ws.app.state.fk.projector.connect(ws)
