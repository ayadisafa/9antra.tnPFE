"""What the market asks: every program against real job offers."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.market_offer import MarketOffer
from app.services import market, program_icons
from app.services.schedule_utils import utc_to_local
from app.template_context import notification_context

router = APIRouter(tags=["Market"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])


def _back(notice: str | None = None, error: str | None = None, anchor: str = "") -> RedirectResponse:
    query = urlencode({key: value for key, value in (("notice", notice), ("error", error)) if value})
    return RedirectResponse(f"/programs/market{'?' + query if query else ''}{anchor}", status_code=303)


@router.get("/programs/market", response_class=HTMLResponse)
def market_page(request: Request, db: Session = Depends(get_db)):
    view = market.overview(db)
    for position, row in enumerate(view["programs"]):
        row["icon"] = program_icons.view(row["program"])
        row["cover"] = program_icons.cover(position)
    return templates.TemplateResponse(
        request=request,
        name="market.html",
        context={
            "active_page": "courses",
            **view,
            "last_read_local": utc_to_local(view["last_read"]) if view["last_read"] else None,
            "enough": market.ENOUGH,
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/programs/market/read")
def read_market(db: Session = Depends(get_db)):
    """Read the public job board now (at most every six hours)."""
    outcome = market.read_public(db)
    if outcome["state"] == "unreachable":
        return _back(error="The job boards could not be reached. Check the internet connection and try again.")
    if outcome["state"] == "recent":
        return _back(notice=f"The job boards were read less than six hours ago: their {outcome['kept']} offers are still fresh.")
    return _back(notice=f"The job boards were read: {outcome['new']} new offer{'s' if outcome['new'] != 1 else ''} in the fields you teach, "
                        f"{outcome['kept']} kept in all.")


@router.post("/programs/market/paste")
def paste_offer(text: str = Form(""), title: str = Form(""), db: Session = Depends(get_db)):
    """Add an offer the admin copied, from LinkedIn or anywhere."""
    row = market.add_pasted(db, text, title)
    if row is None:
        return _back(error="Paste the text of the offer: its title, what the job is and what it asks for.", anchor="#paste")
    fields = market.fields_of(row.title, f"{row.title}. {row.text}")
    names = ", ".join(market.level_bank.SUBJECTS[field][0] for field in fields)
    return _back(notice=f"“{row.title[:60]}” was added" + (f" and counted for: {names}." if names else
                        ". It names no field you teach, so it changes no percentage."), anchor="#paste")


@router.post("/programs/market/offers/{offer_id}/delete")
def delete_offer(offer_id: str, db: Session = Depends(get_db)):
    row = db.get(MarketOffer, offer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Offer not found")
    db.delete(row)
    db.commit()
    return _back(notice="The offer was removed.", anchor="#paste")
