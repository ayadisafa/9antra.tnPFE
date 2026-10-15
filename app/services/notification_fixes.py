"""What a notification lets the admin do at once, without opening a page.

A notification says that something needs them. When the remedy is one action,
it is offered as a button inside the notification: {"label", "url", "method",
"fields", "confirm"}. It is worked out when the notification is shown, from
how things are now, so a problem already solved offers nothing.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.classgroup import ClassGroup, ClassStatus
from app.models.notification import Notification
from app.models.registration import PaymentStatus, Registration, RegistrationStatus


def _post(label: str, url: str, fields: dict | None = None, confirm: str | None = None) -> dict:
    return {"label": label, "url": url, "method": "post", "fields": fields or {}, "confirm": confirm}


def _go(label: str, url: str) -> dict:
    return {"label": label, "url": url, "method": "get", "fields": {}, "confirm": None}


def _class_fix(db: Session, class_id: str) -> dict | None:
    from app.services.enrollment_service import enrolled_count, teachers_of, teaches      # here: it imports a lot

    class_group = db.get(ClassGroup, class_id)
    if class_group is None or class_group.status == ClassStatus.COMPLETED:
        return None
    if not enrolled_count(db, class_group.id):
        return _post("Remove this class", f"/classes/{class_group.id}/delete",
                     confirm=f"Remove {class_group.name}? It has no candidate. This cannot be undone.")
    professor = class_group.professor
    if professor is None and not class_group.professor_name:
        if teachers_of(db, class_group.program):
            return _go("Choose a professor", f"/classes/{class_group.id}#parameters")
        return _go("Give this speciality to a professor", "/classes/?tab=professors")
    if professor is not None and class_group.program is not None and not teaches(professor, class_group.program):
        return _post(f"Yes, {professor.name} teaches it", f"/classes/professors/{professor.id}/specialty",
                     fields={"program_id": class_group.program_id})
    return None


def fix_for(db: Session, notification: Notification) -> dict | None:
    """The one action that solves what this notification is about, if there is one."""
    payload = notification.payload if isinstance(notification.payload, dict) else {}
    if notification.category == "REGISTRATION_REQUEST":
        from app.services import registration_requests

        return registration_requests.fix(db, notification)
    if notification.category == "CLASS" and payload.get("class_id"):
        return _class_fix(db, payload["class_id"])
    if notification.category == "ASSIGNMENT_PENDING" and notification.registration_id:
        registration = db.get(Registration, notification.registration_id)
        if (
            registration is not None and registration.class_group_id is None and registration.program_id
            and registration.payment_status == PaymentStatus.PAID and registration.status == RegistrationStatus.PENDING
        ):
            if notification.title == "Waits for their current class to end":
                return _post("Start them now anyway", f"/classes/registration/{registration.id}/decision",
                             fields={"decision": "create_new"},
                             confirm="They follow another class on the same hours. Start them in a new class now anyway?")
            return _post("Create a class for them", f"/classes/registration/{registration.id}/decision",
                         fields={"decision": "create_new"})
    return None
