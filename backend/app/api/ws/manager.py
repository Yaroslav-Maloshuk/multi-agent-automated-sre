"""WebSocket connection manager: routes bus events → subscribed sockets and
accepts control messages (subscribe / HITL review / graph resume-interrupt).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.bus.redis_bus import EventBus
from app.bus.schemas import ClientAction, EventEnvelope, HITLReview, WSClientMessage
from app.core.config import Settings

logger = logging.getLogger(__name__)

HEARTBEAT = "heartbeat.ping"


class ConnectionManager:
    def __init__(self, bus: EventBus, settings: Settings) -> None:
        self._bus = bus
        self._settings = settings
        self._channels: dict[str, set[WebSocket]] = {}
        self._sockets: dict[WebSocket, set[str]] = {}
        self._listener: asyncio.Task[None] | None = None

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._listener is None:
            self._listener = asyncio.create_task(self._dispatch_loop(), name="ws-dispatch")

    async def stop(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            try:
                await self._listener
            except asyncio.CancelledError:
                pass
            self._listener = None

    async def _dispatch_loop(self) -> None:
        """Fan-out every fabric event to sockets subscribed to its channel."""
        try:
            async for envelope in self._bus.stream():
                await self._fanout(envelope)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("ws dispatch loop crashed")
            await asyncio.sleep(1)

    async def _fanout(self, envelope: EventEnvelope) -> None:
        targets: set[WebSocket] = set()
        targets.update(self._channels.get(envelope.channel, set()))
        targets.update(self._channels.get("*", set()))
        for socket in list(targets):
            try:
                await socket.send_json(envelope.model_dump())
            except Exception:  # noqa: BLE001
                await self.disconnect(socket)

    # ── Connection handling ─────────────────────────────────────────────

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._sockets[websocket] = set()
        await websocket.send_json({"type": "connected", "subscribed": []})

    async def disconnect(self, websocket: WebSocket) -> None:
        for channel in self._sockets.pop(websocket, set()):
            subs = self._channels.get(channel)
            if subs:
                subs.discard(websocket)
                if not subs:
                    self._channels.pop(channel, None)

    async def subscribe(self, websocket: WebSocket, channel: str) -> None:
        self._sockets.setdefault(websocket, set()).add(channel)
        self._channels.setdefault(channel, set()).add(websocket)

    async def unsubscribe(self, websocket: WebSocket, channel: str) -> None:
        self._sockets.get(websocket, set()).discard(channel)
        subs = self._channels.get(channel)
        if subs:
            subs.discard(websocket)
            if not subs:
                self._channels.pop(channel, None)

    # ── Inbound control messages ────────────────────────────────────────

    async def handle_inbound(self, websocket: WebSocket, message: WSClientMessage, on_review, on_resume, on_interrupt) -> None:
        if message.action is ClientAction.SUBSCRIBE and message.channel:
            await self.subscribe(websocket, message.channel)
            await websocket.send_json({"type": "subscribed", "channel": message.channel})
        elif message.action is ClientAction.UNSUBSCRIBE and message.channel:
            await self.unsubscribe(websocket, message.channel)
            await websocket.send_json({"type": "unsubscribed", "channel": message.channel})
        elif message.action is ClientAction.HITL_REVIEW:
            review_payload = {
                **message.payload,
                "incident_id": message.incident_id or message.payload.get("incident_id"),
                "run_id": message.run_id or message.payload.get("run_id"),
            }
            await on_review(HITLReview.model_validate(review_payload))
            await websocket.send_json({"type": "review.accepted", "incident_id": message.incident_id})
        elif message.action is ClientAction.GRAPH_RESUME and message.incident_id:
            await on_resume(message.incident_id)
        elif message.action is ClientAction.GRAPH_INTERRUPT and message.incident_id:
            await on_interrupt(message.incident_id)
        elif message.action is ClientAction.PING:
            await websocket.send_json({"type": "pong", "ts": message.payload})

    # ── Heartbeat ───────────────────────────────────────────────────────

    async def heartbeat(self, websocket: WebSocket) -> None:
        while True:
            await asyncio.sleep(self._settings.ws_heartbeat_interval)
            try:
                await websocket.send_json({"type": HEARTBEAT})
            except Exception:  # noqa: BLE001
                return

    async def run_socket(self, websocket: WebSocket, on_review, on_resume, on_interrupt) -> None:
        """Own the socket loop: initial channels + inbound handling + heartbeat."""
        await self.connect(websocket)
        heartbeat = asyncio.create_task(self.heartbeat(websocket))
        try:
            while True:
                raw = await websocket.receive_json()
                try:
                    message = WSClientMessage.model_validate(raw)
                except Exception as exc:  # noqa: BLE001
                    await websocket.send_json({"type": "error", "message": f"invalid message: {exc}"})
                    continue
                await self.handle_inbound(websocket, message, on_review, on_resume, on_interrupt)
        except WebSocketDisconnect:
            pass
        finally:
            heartbeat.cancel()
            await self.disconnect(websocket)
