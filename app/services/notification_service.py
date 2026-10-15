from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.notification import Notification

# ==================================================
# Core Notification Creation Helper
# ==================================================

def create_notification(
    db: Session,
    title: str,
    message: str,
    category: str = "SYSTEM",
    recipient_role: str = "admin",
    candidate_id: str | None = None,
    html_content: str | None = None,
    action_url: str | None = None,
    payload: dict | None = None,
    registration_id: str | None = None,
) -> Notification:
    """
    Creates and persists an advanced notification to the database.
    """
    notification = Notification(
        title=title,
        message=message,
        category=category,
        recipient_role=recipient_role,
        candidate_id=candidate_id,
        registration_id=registration_id,
        html_content=html_content or message,
        action_url=action_url,
        payload=payload,
        is_read=False,
        status="unread"
    )

    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


# ==================================================
# Backward Compatibility Wrapper
# ==================================================

def save_notification(
    db: Session,
    candidate_id: str,
    notification_type: str,
    status: str = "sent",
    message: str | None = None,
    html_content: str | None = None
) -> Notification:
    """
    Legacy wrapper so existing function calls won't break across your codebase.
    """
    return create_notification(
        db=db,
        title=f"Notification: {notification_type}",
        message=message or f"Notification of type {notification_type}",
        category=notification_type,
        candidate_id=candidate_id,
        html_content=html_content
    )


# ==================================================
# Browser Dashboard & Management Helpers
# ==================================================

def get_all_notifications(
    db: Session, 
    recipient_role: str = "admin", 
    limit: int = 50, 
    unread_only: bool = False
):
    """Fetch recent notifications for admin dashboard or user drawer."""
    query = db.query(Notification).filter(Notification.recipient_role == recipient_role)
    
    if unread_only:
        query = query.filter(Notification.is_read == False)

    return query.order_by(Notification.created_at.desc()).limit(limit).all()


def get_candidate_notifications(db: Session, candidate_id: str, limit: int = 20):
    """Fetch notifications specifically targeted to a single candidate."""
    return (
        db.query(Notification)
        .filter(Notification.candidate_id == candidate_id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .all()
    )


def get_unread_count(db: Session, recipient_role: str = "admin") -> int:
    """Gets total unread count for badges/notification bell."""
    return (
        db.query(Notification)
        .filter(
            Notification.recipient_role == recipient_role,
            Notification.is_read == False
        )
        .count()
    )


def mark_notification_as_read(db: Session, notification_id: str):
    """Mark a specific notification as 'read' from the UI."""
    notification = db.query(Notification).filter(Notification.id == notification_id).first()
    if notification:
        notification.is_read = True
        notification.status = "read"
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(notification)
    return notification


def mark_all_as_read(db: Session, recipient_role: str = "admin"):
    """Mark all pending notifications as read for a given recipient role."""
    db.query(Notification).filter(
        Notification.recipient_role == recipient_role,
        Notification.is_read == False
    ).update({
        "is_read": True,
        "status": "read",
        "read_at": datetime.now(timezone.utc)
    })
    db.commit()
    return True