"""Server-sent events for the admin UI.

Two event types are pushed to every open page:

* ``notification`` - a new admin notification (bell, badge, toast)
* ``change``       - topics whose data changed, so pages showing them refresh

Both are derived from committed database writes (see ``install_session_hooks``),
so every code path - admin actions, the scheduler, the Firebase listener -
is real-time without having to publish anything itself.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading

from sqlalchemy import event, inspect

logger = logging.getLogger(__name__)

_CLOSE = object()


class Broadcaster:
    """Fan-out of events to SSE subscribers; `publish` is safe from any thread."""

    def __init__(self) -> None:
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()
        self.closing = False

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subscribers.add((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = {item for item in self._subscribers if item[1] is not queue}

    @staticmethod
    def _offer(queue: asyncio.Queue, message) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(message)

    def _send(self, message) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._offer, queue, message)
            except RuntimeError:
                # Loop already closed; the subscriber is gone.
                self.unsubscribe(queue)

    def publish(self, event_name: str, data: dict) -> None:
        payload = json.dumps(data, default=str)
        self._send(f"event: {event_name}\ndata: {payload}\n\n")

    def close_all(self) -> None:
        """End every open stream so the server can shut down promptly."""
        self.closing = True
        self._send(_CLOSE)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


broadcaster = Broadcaster()


def publish_change(*topics: str) -> None:
    topics = sorted({topic for topic in topics if topic})
    if topics:
        broadcaster.publish("change", {"topics": topics})


def serialize_notification(notification) -> dict:
    return {
        "id": notification.id,
        "title": notification.title,
        "message": notification.message,
        "category": notification.category,
        "candidate_id": notification.candidate_id,
        "program_id": notification.program_id,
        "action_url": notification.action_url,
        "similarity_score": notification.similarity_score,
        "is_read": False,
    }


def _topics_for(obj, deleted: bool = False) -> set[str]:
    """Which UI topics an added, changed or deleted row affects."""
    name = type(obj).__name__
    topics: set[str] = set()

    if name == "Registration":
        topics |= {"candidates", "classes", "dashboard", f"candidate:{obj.candidate_id}"}
        if obj.class_group_id:
            topics.add(f"class:{obj.class_group_id}")
        if not deleted:
            # A moved registration also changes the class it left.
            history = inspect(obj).attrs.class_group_id.history
            topics |= {f"class:{old}" for old in history.deleted if old}
    elif name == "ClassGroup":
        topics |= {"classes", "dashboard", f"class:{obj.id}"}
    elif name == "ClassSession":
        topics |= {"classes", f"class:{obj.class_group_id}"}
    elif name == "Candidate":
        topics |= {"candidates", "dashboard", f"candidate:{obj.id}"}
    elif name == "Payment":
        topics |= {"candidates", "dashboard"}
    elif name in ("CandidateReminder", "CandidateEmail", "Activity"):
        topics.add("reminders" if name == "CandidateReminder" else "activity")
        if name == "CandidateEmail":
            topics.add("inbox")
        if getattr(obj, "candidate_id", None):
            topics.add(f"candidate:{obj.candidate_id}")
    elif name == "EmailThread":
        topics |= {"inbox", f"candidate:{obj.candidate_id}"}
    elif name == "KnowledgeEntry":
        topics.add("knowledge")
    elif name == "EmailTemplate":
        topics.add("email_templates")
    elif name == "Professor":
        topics |= {"classes", "dashboard"}
    elif name == "SystemSettings":
        topics |= {"settings", "classes", "system"}
    elif name in ("Program", "ProgramProposal"):
        topics |= {"programs", "dashboard"}
    elif name in ("ClassMaterial", "ClassWeek", "WeekQuestion"):
        topics |= {"dashboard", f"course:{obj.class_group_id}"}
    elif name == "Quiz":
        topics.add("levels")
        if getattr(obj, "candidate_id", None):
            topics.add(f"candidate:{obj.candidate_id}")
        if getattr(obj, "class_group_id", None):
            topics |= {"dashboard", f"course:{obj.class_group_id}", f"class:{obj.class_group_id}"}
    elif name == "AdviceSnooze":
        topics.add("dashboard")
    elif name == "TeamMessage":
        topics |= {"team", f"team:{obj.person}"}
    elif name == "CandidateFeedback":
        topics |= {"reviews", "programs", "dashboard"}
        if getattr(obj, "candidate_id", None):
            topics.add(f"candidate:{obj.candidate_id}")
    elif name in ("MarketOffer", "MarketSignal"):
        topics |= {"market", "programs"}
    elif name in ("WebsiteItem", "WebsiteCheck"):
        topics |= {"website", "programs", "dashboard"}
    elif name == "User":
        topics.add("team_people")
    elif name == "ResourceReminder":
        topics |= {"classes", f"course:{obj.class_group_id}"}
    elif name == "Attendance":
        topics |= {"classes", "dashboard", f"class:{obj.class_group_id}", f"candidate:{obj.candidate_id}"}
    elif name == "AdminReminder":
        topics.add("reminders")
    elif name in ("ProfessorAbsence", "ProfessorAvailability"):
        topics |= {"classes", "dashboard"}
    elif name in ("ProgramLevel", "WeeklyCurriculum", "CurriculumResource"):
        topics |= {"programs"}
    elif name == "SessionCheck":
        topics |= {"classes", f"class:{obj.class_group_id}", f"course:{obj.class_group_id}"}
    elif name == "ProfessorAnnouncement":
        topics |= {"classes", "team"}
    elif name == "PlannedResend":
        topics |= {"system", "reminders", "dashboard"}
    elif name == "UiTranslation":
        topics.add("i18n")
    elif name == "PaymentProof":
        topics |= {"payments", "dashboard"}
        if getattr(obj, "candidate_id", None):
            topics.add(f"candidate:{obj.candidate_id}")
    return topics


def install_session_hooks(session_factory) -> None:
    """Publish notifications and change topics whenever a session commits."""

    @event.listens_for(session_factory, "after_flush")
    def _collect(session, flush_context):
        notifications = session.info.setdefault("rt_notifications", [])
        topics = session.info.setdefault("rt_topics", set())
        for obj in session.new:
            if type(obj).__name__ == "Notification":
                if (obj.recipient_role or "admin") == "admin":
                    notifications.append(serialize_notification(obj))
            else:
                topics |= _topics_for(obj)
        for obj in session.dirty:
            if session.is_modified(obj):
                topics |= _topics_for(obj)
        for obj in session.deleted:
            topics |= _topics_for(obj, deleted=True)

    @event.listens_for(session_factory, "after_commit")
    def _publish(session):
        notifications = session.info.pop("rt_notifications", [])
        topics = session.info.pop("rt_topics", set())
        try:
            for notification in notifications:
                broadcaster.publish("notification", notification)
            if topics:
                publish_change(*topics)
        except Exception:
            logger.exception("Realtime publish failed.")

    @event.listens_for(session_factory, "after_rollback")
    def _discard(session):
        session.info.pop("rt_notifications", None)
        session.info.pop("rt_topics", None)


async def event_stream(request, notifications: bool = True):
    """Async generator backing GET /events/stream. Without `notifications`, only the changes go out."""
    queue = broadcaster.subscribe()
    try:
        yield "retry: 3000\n\n"
        while not broadcaster.closing:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    break
                yield ": ping\n\n"
                continue
            if message is _CLOSE:
                break
            if not notifications and message.startswith("event: notification"):
                continue
            yield message
    finally:
        broadcaster.unsubscribe(queue)
