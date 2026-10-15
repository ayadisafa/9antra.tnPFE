from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.notification import Notification
from app.services import notification_fixes
from app.services.notification_service import mark_all_as_read, mark_notification_as_read


router = APIRouter(prefix="/admin/api/notifications", tags=["Notifications"])


def _serialize(notification: Notification, db: Session | None = None) -> dict:
    return {
        "fix": notification_fixes.fix_for(db, notification) if db is not None else None,
        "id": notification.id,
        "title": notification.title,
        "message": notification.message,
        "category": notification.category,
        "candidate_id": notification.candidate_id,
        "program_id": notification.program_id,
        "action_url": notification.action_url,
        "is_read": bool(notification.is_read),
        "status": notification.status,
        "similarity_score": notification.similarity_score,
        "created_at": notification.created_at.strftime("%m-%d %H:%M") if notification.created_at else "Just now",
    }


@router.get("")
def get_notifications(
    page: int = Query(1, ge=1),
    unread_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    per_page = 10
    query = db.query(Notification).filter(Notification.recipient_role == "admin")
    if unread_only:
        query = query.filter(Notification.is_read.is_(False))
    offset = (page - 1) * per_page
    total_items = query.count()
    notifications = query.order_by(Notification.created_at.desc()).offset(offset).limit(per_page).all()
    return {
        "notifications": [_serialize(item, db) for item in notifications],
        "unread_count": db.query(Notification).filter(
            Notification.recipient_role == "admin",
            Notification.is_read.is_(False),
        ).count(),
        "has_more": offset + per_page < total_items,
    }


@router.get("/{notification_id}/fix")
def fix_of(notification_id: str, db: Session = Depends(get_db)):
    """The one action that solves what a notification is about, as things are now."""
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"fix": notification_fixes.fix_for(db, notification)}


@router.post("/{notification_id}/read")
def mark_read(notification_id: str, db: Session = Depends(get_db)):
    notification = mark_notification_as_read(db, notification_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"success": True, "notification": _serialize(notification)}


@router.post("/read-all")
def mark_all_read(db: Session = Depends(get_db)):
    mark_all_as_read(db, "admin")
    return {"success": True}
