"""WebSocket gateway: the bidirectional contract surface.

Client connects with `?channels=incident:...,telemetry` (comma separated) or
subscribes at runtime via `{action: "subscribe", channel: "..."}` messages.
The server fans every Redis-bus event back out in the `EventEnvelope` schema.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.api.ws.manager import ConnectionManager
from app.bus.schemas import HITLReview, WSClientMessage
from app.core.errors import AppError, GraphNotReadyError

router = APIRouter()


@router.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket) -> None:
    app = websocket.app
    manager: ConnectionManager = app.state.ws_manager
    service = app.state.incident_service

    channels = _parse_channels(websocket.query_params.get("channels", ""))
    await manager.connect(websocket)
    for channel in channels:
        await manager.subscribe(websocket, channel)
    if channels:
        await websocket.send_json({"type": "subscribed", "channels": list(channels)})

    async def on_review(review: HITLReview) -> None:
        service.review(review)

    async def on_resume(incident_id: str) -> None:
        await service.resume(incident_id)

    async def on_interrupt(incident_id: str) -> None:
        await service.interrupt(incident_id)

    heartbeat = asyncio.create_task(manager.heartbeat(websocket))
    try:
        while True:
            raw = await websocket.receive_json()
            try:
                message = WSClientMessage.model_validate(raw)
            except Exception as exc:  # noqa: BLE001
                await websocket.send_json({"type": "error", "message": f"invalid message: {exc}"})
                continue
            try:
                await manager.handle_inbound(websocket, message, on_review, on_resume, on_interrupt)
            except GraphNotReadyError as exc:
                await websocket.send_json({"type": "error", "error": exc.details or {"message": exc.message}})
            except AppError as exc:
                await websocket.send_json({"type": "error", "error": {"code": exc.code, "message": exc.message}})
    except WebSocketDisconnect:
        pass
    finally:
        heartbeat.cancel()
        if websocket.client_state is WebSocketState.CONNECTED:
            await websocket.close()
        await manager.disconnect(websocket)


def _parse_channels(raw: str) -> set[str]:
    return {c.strip() for c in raw.split(",") if c.strip()}
