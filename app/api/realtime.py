from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.realtime import event_stream

router = APIRouter(tags=["Realtime"])


@router.get("/events/stream")
async def stream_events(request: Request):
    """Server-sent events: new notifications and data changes, pushed to every open page."""
    return StreamingResponse(
        event_stream(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
