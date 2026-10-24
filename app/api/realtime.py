from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services import auth
from app.services.realtime import event_stream

router = APIRouter(tags=["Realtime"])


@router.get("/events/stream")
async def stream_events(request: Request):
    """Server-sent events: data changes, pushed to every open page so it redraws itself; and, for the admin, the new
    notifications. A professor or a team member gets the changes only (topic names, never the notifications' words)."""
    user = auth.current(request)
    return StreamingResponse(
        event_stream(request, notifications=user is not None and user.role == auth.ADMIN),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
