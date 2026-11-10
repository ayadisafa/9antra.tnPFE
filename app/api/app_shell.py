"""The application as a phone app: its service worker (notifications when it is closed), the phones that said yes,
the pictures notifications show, and the iPhone's link to send the waiting SMS (services/phone_push.py, phone_sms.py)."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import auth, phone_push, phone_sms, theme
from app.services.settings_service import get_settings
from app.services.ui_words import t as _t

router = APIRouter(tags=["Phone app"])

# The service worker: it receives the notifications while the application is closed, shows them, and opens the exact
# place when one is tapped. Served from the root so it covers every page.
WORKER = r"""/* 9antra: notifications on this phone, even with the application closed. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("push", event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (error) { data = { body: event.data ? event.data.text() : "" }; }
  const shown = self.registration.showNotification(data.title || "9antra", {
    body: data.body || "", icon: data.icon || "/static/brand/icon-192.png", image: data.image || undefined,
    badge: data.badge || undefined, tag: data.tag || undefined, renotify: !!data.tag, timestamp: Date.now(),
    vibrate: [60, 40, 60], data: { url: data.url || "/" }
  });
  const badge = (typeof data.count === "number" && self.navigator && self.navigator.setAppBadge)
    ? self.navigator.setAppBadge(data.count).catch(() => {}) : Promise.resolve();
  event.waitUntil(Promise.all([shown, badge]));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
    for (const client of list) {
      if ("focus" in client) return client.focus().then(found => (found && found.navigate) ? found.navigate(url) : found);
    }
    return self.clients.openWindow(url);
  }));
});
"""


@router.get("/sw.js")
def service_worker():
    return Response(WORKER, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@router.get("/notify-picture/{category}.png")
def notification_picture(category: str):
    """The picture of a kind of notification (an emoji on a card of its colour)."""
    if not category.replace("_", "").isalnum() or len(category) > 40:
        raise HTTPException(status_code=404)
    return Response(phone_push.picture_png(category.upper()), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


def _me(request: Request):
    user = auth.current(request)
    if user is None:
        raise HTTPException(status_code=401)
    return user


@router.get("/account/look")
def look_now(db: Session = Depends(get_db)):
    """The design as it is now: an open page takes it when it changed on another page or device (base.html)."""
    return theme.look(get_settings(db))


@router.get("/account/push/key")
def push_key(request: Request, db: Session = Depends(get_db)):
    me = _me(request)
    return {"key": phone_push.public_key(),
            "devices": [{"id": row.id, "device": row.device, "level": row.level} for row in phone_push.devices(db, me)]}


@router.post("/account/push/subscribe")
async def push_subscribe(request: Request, db: Session = Depends(get_db)):
    me = _me(request)
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(status_code=400)
    row = phone_push.subscribe(db, me, data, request.headers.get("user-agent", ""))
    if row is None:
        raise HTTPException(status_code=400, detail="Not a phone subscription")
    return {"ok": True, "id": row.id, "device": row.device}


@router.post("/account/push/unsubscribe")
async def push_unsubscribe(request: Request, db: Session = Depends(get_db)):
    me = _me(request)
    try:
        data = await request.json()
    except ValueError:
        data = {}
    return {"ok": phone_push.unsubscribe(db, me, endpoint=data.get("endpoint"), row_id=data.get("id"))}


@router.post("/account/push/level")
def push_level(request: Request, id: str = Form(""), level: str = Form("important"), db: Session = Depends(get_db)):
    me = _me(request)
    for row in phone_push.devices(db, me):
        if row.id == id and level in phone_push.LEVELS:
            row.level = level
    db.commit()
    return RedirectResponse("/account#phone", status_code=303)


@router.post("/account/push/test")
def push_test(request: Request, db: Session = Depends(get_db)):
    me = _me(request)
    rows = phone_push.devices(db, me)
    took = phone_push.send(db, rows, {"title": "9antra", "body": _t("Notifications reach this phone: you will be told here, even with the application closed."),
                                      "url": "/account#phone", "icon": "/static/brand/icon-192.png", "image": "/notify-picture/GENERAL.png", "tag": "test"})
    return {"ok": took > 0, "sent": took, "devices": len(rows)}


# ----------------------------------------------------------------------
# The iPhone's shortcut: the SMS waiting, and which went
# ----------------------------------------------------------------------

@router.get("/phone-sms/{secret}/waiting")
def sms_waiting(secret: str, db: Session = Depends(get_db)):
    if not phone_sms.allowed(db, secret):
        raise HTTPException(status_code=404)
    found = phone_sms.waiting(db)
    return JSONResponse({"count": len(found), "messages": found}, headers={"Cache-Control": "no-store"})


@router.post("/phone-sms/{secret}/sent/{key}")
def sms_sent(secret: str, key: str, db: Session = Depends(get_db)):
    if not phone_sms.allowed(db, secret):
        raise HTTPException(status_code=404)
    return {"ok": phone_sms.mark_sent(db, key)}


@router.post("/settings/sms/iphone-link")
def sms_new_link(request: Request, db: Session = Depends(get_db)):
    """A new secret for the iPhone's link: the old link stops working (the shortcut then needs the new one)."""
    phone_sms.token(db, renew=True)
    return RedirectResponse("/settings?" + urlencode({"notice": _t("A new link for your iPhone is ready: put it in the shortcut.")}) + "#sms", status_code=303)
