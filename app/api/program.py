import asyncio
import io
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from urllib.parse import urlencode

from fastapi import APIRouter, File, status, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pypdf import PdfReader
from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer, util
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models.classgroup import ClassGroup, ClassStatus
from app.models.program import Program, ProgramLevel, WeeklyCurriculum
from app.models.registration import PaymentStatus, Registration, RegistrationStatus
from app.models.website import WebsiteItem
from app.services import class_looks, email_assistant, level_bank, level_check, market, program_assistant, program_icons
from app.services.class_views import class_summary
from app.services.enrollment_service import teachers_of
from app.services.schedule_utils import local_now
from app.template_context import notification_context

# Safe imports for optional activity and notification models
from app.models.activity import Activity as ActivityLog

try:
    from app.models.notification import Notification
except (ImportError, AttributeError):
    Notification = None

# Load environment variables
BASE_DIR = Path(__file__).resolve().parent.parent.parent
env_path = BASE_DIR / ".env"
load_dotenv(dotenv_path=env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

router = APIRouter(prefix="/programs", tags=["Programs"])
templates = Jinja2Templates(directory="app/templates", context_processors=[notification_context])

# Initialize SentenceTransformer model for semantic deduplication & fusion
transformer_model = SentenceTransformer("all-MiniLM-L6-v2")


# ==========================================
# HELPER & HYBRID DEDUPLICATION SERVICES
# ==========================================

def get_most_frequent_prices(db) -> tuple[float, float]:
    """Calculates the most frequent price_online and price_onsite across existing programs."""
    online_res = (
        db.query(Program.price_online, func.count(Program.price_online).label("freq"))
        .filter(Program.price_online > 0, Program.price_online.isnot(None))
        .group_by(Program.price_online)
        .order_by(func.count(Program.price_online).desc())
        .first()
    )
    frequent_online = online_res[0] if online_res else 150.0

    onsite_res = (
        db.query(Program.price_onsite, func.count(Program.price_onsite).label("freq"))
        .filter(Program.price_onsite > 0, Program.price_onsite.isnot(None))
        .group_by(Program.price_onsite)
        .order_by(func.count(Program.price_onsite).desc())
        .first()
    )
    frequent_onsite = onsite_res[0] if onsite_res else 300.0

    return frequent_online, frequent_onsite


def generate_embedding(name: str, description: str = "") -> list[float]:
    """Generates a 384-dimensional vector embedding using sentence-transformers."""
    text_content = f"{name.strip()}. {description.strip() if description else ''}".strip()
    tensor = transformer_model.encode(text_content, convert_to_tensor=False)
    return tensor.tolist()


def ensure_all_embeddings(db: Session):
    """Fills missing embeddings for existing database programs."""
    programs = db.query(Program).filter(Program.embedding.is_(None)).all()
    for p in programs:
        vector = generate_embedding(p.name, p.description or "")
        p.set_embedding(vector)
    if programs:
        db.commit()


def normalize_title(text: str) -> str:
    """Strips parentheses, special characters, and normalizes title tokens."""
    text = text.lower()
    text = re.sub(r'\(.*?\)', '', text)  # Strips parentheses (e.g., "(BI)")
    text = re.sub(r'[^a-z0-9\s]', '', text)
    return ' '.join(text.split())


def compute_hybrid_similarity(name1: str, desc1: str, name2: str, desc2: str) -> float:
    """
    Evaluates similarity using 3 signals:
    1. RapidFuzz token_set_ratio (catches title order changes like 'BI (Business Intelligence)')
    2. SentenceTransformer isolated title vectors (prevents missing description penalties)
    3. SentenceTransformer full text vectors (Title + Description)
    """
    # 1. Normalized Lexical Token Set Match
    norm1, norm2 = normalize_title(name1), normalize_title(name2)
    fuzzy_score = fuzz.token_set_ratio(norm1, norm2) / 100.0

    # 2. Vector Embedding Match (Title-only)
    v_title1 = transformer_model.encode(name1.strip(), convert_to_tensor=True)
    v_title2 = transformer_model.encode(name2.strip(), convert_to_tensor=True)
    title_vector_score = float(util.cos_sim(v_title1, v_title2)[0][0])

    # 3. Vector Embedding Match (Full Text)
    full_text1 = f"{name1.strip()}. {desc1.strip() if desc1 else ''}".strip()
    full_text2 = f"{name2.strip()}. {desc2.strip() if desc2 else ''}".strip()
    
    v_full1 = transformer_model.encode(full_text1, convert_to_tensor=True)
    v_full2 = transformer_model.encode(full_text2, convert_to_tensor=True)
    full_vector_score = float(util.cos_sim(v_full1, v_full2)[0][0])

    # Select strongest matching signal
    best_score = max(fuzzy_score, title_vector_score, full_vector_score)
    return round(best_score, 4)


def find_semantic_duplicate(db: Session, name: str, description: str = "", threshold: float = 0.75) -> tuple[Program | None, float]:
    """Finds an existing duplicate program using the hybrid matcher."""
    existing_programs = db.query(Program).all()
    if not existing_programs:
        return None, 0.0

    best_match = None
    best_score = 0.0

    for prog in existing_programs:
        score = compute_hybrid_similarity(
            name, description,
            prog.name, prog.description or ""
        )
        if score > best_score:
            best_score = score
            best_match = prog

    if best_score >= threshold:
        return best_match, best_score

    return None, best_score


def merge_program_records(db: Session, primary: Program, duplicate: Program):
    """Fuses 'duplicate' into 'primary', combining details and updating foreign keys."""
    if not primary.description and duplicate.description:
        primary.description = duplicate.description
    if (not primary.total_duration_days or primary.total_duration_days == 0) and duplicate.total_duration_days:
        primary.total_duration_days = duplicate.total_duration_days

    # Re-link child curriculum levels
    db.query(ProgramLevel).filter(ProgramLevel.program_id == duplicate.id).update(
        {"program_id": primary.id}, synchronize_session=False
    )

    # Re-link secondary foreign keys safely
    for table, col in [("registrations", "program_id"), ("notifications", "program_id"), ("class_groups", "program_id")]:
        try:
            db.execute(
                text(f"UPDATE {table} SET {col} = :p_id WHERE {col} = :d_id"),
                {"p_id": primary.id, "d_id": duplicate.id}
            )
        except Exception:
            pass

    # Recompute vector embedding for primary program
    primary.set_embedding(generate_embedding(primary.name, primary.description or ""))
    db.delete(duplicate)
    db.flush()


def call_gemini_api_direct(prompt: str, model_name: str) -> str:
    """Calls the Gemini REST API directly using standard urllib."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured in .env file.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            candidates = res_json.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
            return ""
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise RuntimeError(f"Gemini API HTTP {e.code}: {error_body}")
    except Exception as e:
        raise RuntimeError(f"Failed to communicate with Gemini API: {str(e)}")


# ==========================================
# BASE PROGRAM MANAGEMENT ROUTES
# ==========================================


def _back_to_programs(notice: str | None = None, error: str | None = None, review: str | None = None) -> RedirectResponse:
    """303 to the programs page with a one-line message, optionally reopening a review."""
    query = urlencode({
        key: value for key, value in (("review", review), ("notice", notice), ("error", error)) if value
    })
    return RedirectResponse(f"/programs/all{'?' + query if query else ''}", status_code=303)


def _get_program(db: Session, program_id: str) -> Program:
    program = db.get(Program, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Program not found")
    return program


def program_cards(db: Session) -> list[dict]:
    """Every program with what its card shows: picture, people, classes, what is missing."""
    programs = db.query(Program).order_by(Program.name.asc()).all()
    registered: dict[str, dict] = {}
    rows = (
        db.query(Registration.program_id, Registration.payment_status, func.count(Registration.id))
        .filter(Registration.program_id.isnot(None), Registration.status != RegistrationStatus.CANCELLED)
        .group_by(Registration.program_id, Registration.payment_status)
        .all()
    )
    for program_id, payment_status, count in rows:
        entry = registered.setdefault(program_id, {"total": 0, "paid": 0})
        entry["total"] += count
        if payment_status == PaymentStatus.PAID:
            entry["paid"] += count
    classes: dict[str, int] = {}
    for program_id, count in (
        db.query(ClassGroup.program_id, func.count(ClassGroup.id))
        .filter(ClassGroup.status != ClassStatus.COMPLETED)
        .group_by(ClassGroup.program_id)
        .all()
    ):
        classes[program_id] = count
    proposals = program_assistant.open_proposals(db)

    cards = []
    for position, program in enumerate(programs):
        people = registered.get(program.id, {"total": 0, "paid": 0})
        cards.append({
            "program": program,
            "icon": program_icons.view(program),
            "cover": program_icons.cover(position),
            "missing": program_assistant.missing_labels(program),
            "proposal": proposals.get(program.id),
            "registrations": people["total"],
            "paid": people["paid"],
            "classes": classes.get(program.id, 0),
            "levels": len(program.levels),
            "weeks": sum(len(level.weeks) for level in program.levels),
        })
    return cards


@router.get("/all")
def list_programs(request: Request, db: Session = Depends(get_db)):
    cards = program_cards(db)
    review = None
    review_id = request.query_params.get("review")
    if review_id:
        review = next((card for card in cards if card["program"].id == review_id), None)

    return templates.TemplateResponse(
        request=request,
        name="programs.html",
        context={
            "active_page": "courses",
            "cards": cards,
            "review": review,
            "fields": program_assistant.FIELDS,
            "model_ready": email_assistant.model_available(),
            "stats": {
                "total": len(cards),
                "complete": sum(1 for card in cards if not card["missing"]),
                "incomplete": sum(1 for card in cards if card["missing"]),
                "to_review": sum(1 for card in cards if card["proposal"] and card["proposal"].status == "ready"),
                "writing": sum(1 for card in cards if card["proposal"] and card["proposal"].status in ("queued", "writing")),
                "pending": sum(1 for card in cards if not card["program"].is_approved),
                "registrations": sum(card["registrations"] for card in cards),
            },
            "status_filter": request.query_params.get("status") or "all",
            "notice": request.query_params.get("notice"),
            "error": request.query_params.get("error"),
        },
    )


# ==========================================
# PICTURES
# ==========================================

@router.get("/icons/search")
def search_icons(q: str = ""):
    """Icons of the web catalogue matching the admin's words."""
    found = program_icons.search(q, limit=48)
    if found is None:
        return JSONResponse({"error": "The icon catalogue could not be reached. Check the internet connection."}, status_code=502)
    return {"icons": [{"ref": ref, "url": program_icons.preview_url(ref)} for ref in found]}


@router.get("/{program_id}/icon")
def program_icon(program_id: str, db: Session = Depends(get_db)):
    program = _get_program(db, program_id)
    path = program_icons.path_of(program)
    if path is None:
        raise HTTPException(status_code=404, detail="This program has no picture")
    return FileResponse(
        path,
        media_type=program_icons.media_type(program),
        headers={
            # The address changes with the picture, so it can be kept for long.
            "Cache-Control": "private, max-age=604800",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        },
    )


@router.post("/{program_id}/icon")
async def change_program_icon(
    program_id: str,
    action: str = Form("choose"),
    ref: str = Form(""),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    """Choose an icon of the catalogue, upload a picture, look again on the web, or remove it."""
    program = _get_program(db, program_id)
    if action == "remove":
        program_icons.remove(db, program)
        return _back_to_programs(notice=f"{program.name} now shows its initials.")
    if action == "auto":
        outcome = program_icons.assign_from_web(db, program)
        messages = {
            "ok": (f"A picture was found for {program.name}.", None),
            "nothing": (f"No icon on the web fits “{program.name}” exactly: a general picture is shown. Search with your own words, or upload one.", None),
            "offline": (f"The icon catalogue could not be reached: a picture was drawn for {program.name}; a better one is looked for later.", None),
        }
        notice, error = messages[outcome]
        return _back_to_programs(notice=notice, error=error)
    if action == "upload":
        if file is None or not file.filename:
            return _back_to_programs(error="Choose a picture to upload.")
        data = await file.read(program_icons.MAX_UPLOAD_BYTES + 1)
        try:
            program_icons.save_upload(db, program, file.filename, data)
        except ValueError as exc:
            return _back_to_programs(error=str(exc))
        return _back_to_programs(notice=f"Your picture is now shown for {program.name}.")
    if program_icons.assign_from_web(db, program, ref=ref.strip()) != "ok":
        return _back_to_programs(error="This icon could not be downloaded. Choose another one.")
    return _back_to_programs(notice=f"Picture changed for {program.name}.")


# ==========================================
# PROPOSALS FOR MISSING DETAILS
# ==========================================

@router.post("/{program_id}/proposal/request")
def request_proposal(program_id: str, db: Session = Depends(get_db)):
    """Ask the system to prepare (again) what this program misses."""
    program = _get_program(db, program_id)
    proposal = program_assistant.request(db, program)
    if proposal is None:
        return _back_to_programs(notice=f"{program.name} has all its details.")
    return _back_to_programs(
        notice=f"The proposal for {program.name} is being prepared. It takes a few minutes; you will be notified.",
    )


@router.post("/{program_id}/proposal/accept")
async def accept_proposal(program_id: str, request: Request, db: Session = Depends(get_db)):
    """Save the proposal, as the admin corrected it, on the program."""
    program = _get_program(db, program_id)
    proposal = program_assistant.open_proposal(db, program.id)
    if proposal is None or proposal.status != "ready":
        return _back_to_programs(error="This proposal is no longer waiting.")
    data = program_assistant.read_form(await request.form())
    if not data:
        return _back_to_programs(error="Nothing to save: every field of the proposal is empty.", review=program.id)
    done = program_assistant.accept(db, proposal, data)
    if not done["filled"]:
        return _back_to_programs(notice=f"{program.name} already had these details; nothing was changed.")
    notice = f"{program.name}: {', '.join(done['filled']).lower()} saved."
    if done["classes"]:
        notice += f" Weekly plan created for {', '.join(done['classes'])}."
    return _back_to_programs(notice=notice)


@router.post("/{program_id}/proposal/dismiss")
def dismiss_proposal(program_id: str, db: Session = Depends(get_db)):
    program = _get_program(db, program_id)
    proposal = program_assistant.open_proposal(db, program.id)
    if proposal is not None:
        program_assistant.dismiss(db, proposal)
    return _back_to_programs(notice=f"Proposal for {program.name} dismissed. The program is unchanged.")




@router.get("/upload")
def upload_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="program_upload.html",
        context={"results": None}
    )


@router.post("/upload")
async def process_program_pdf(request: Request, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        return templates.TemplateResponse(
            request=request,
            name="program_upload.html",
            context={"error": "Please select a valid PDF file.", "results": None}
        )
    try:
        contents = await file.read()
        pdf_reader = PdfReader(io.BytesIO(contents))
        
        extracted_pages = []
        for idx, page in enumerate(pdf_reader.pages, start=1):
            text_content = page.extract_text()
            if text_content and text_content.strip():
                extracted_pages.append(f"--- PAGE {idx} ---\n{text_content}")

        full_extracted_text = "\n\n".join(extracted_pages)

        if not full_extracted_text.strip():
            return templates.TemplateResponse(
                request=request,
                name="program_upload.html",
                context={
                    "error": "Could not extract text from this PDF. It might be scanned or image-only.",
                    "results": None
                }
            )

        prompt = f"""
        You are an expert curriculum data extractor. 
        Analyze the provided PDF catalog text and extract EVERY SINGLE training program listed across all pages, including detailed breakdown of prices, session hours, levels, and week-by-week curriculum.

        CRITICAL FILTERING & EXTRACTION RULES:
        1. **Course Name**: Extract title from "COURSE PLAN (CourseName)" or headers, e.g. "DevOps".
        2. **Levels & Bullet Points**: 
           - Read level blocks carefully (Level 1, Level 2, Level 3).
           - Under each level, take all listed topics/technologies (e.g. Docker, GitHub, Jenkins). Map each as an entry in `weeks` with incremental `week_number` (1, 2, 3...).
        3. **Pricing**: 
           - Look for "Face to face course" or "Onsite" discounted price (e.g. "490 DT/Month" -> price_onsite = 490.0). Ignore crossed-out original prices.
           - Look for "Online course" discounted price (e.g. "390 DT/Month" -> price_online = 390.0). Ignore crossed-out original prices.
        4. **Timings**:
           - `Duration: X months` -> set `total_duration_days` to X * 30 (e.g. "2 months" -> 60).
           - `Number of hours per week` -> extract number (e.g. 6).
           - `Training` -> extract hours (e.g. 4).
           - `Supervision` -> extract hours (e.g. 2).
        5. DO NOT omit, summarize, or skip any programs. Process every single page content completely.

        REQUIRED JSON OUTPUT FORMAT:
        {{
          "programs": [
            {{
              "name": "Program Title",
              "description": "Short summary",
              "total_duration_days": 60,
              "price_online": 390.0,
              "price_onsite": 490.0,
              "hours_per_week": 6,
              "training_hours": 4,
              "supervision_hours": 2,
              "class_session_time": "18:00 - 21:00",
              "levels": [
                {{
                  "level_order": 1,
                  "title": "Level 1: Fundamentals",
                  "description": "Level summary",
                  "hours_per_week": 6,
                  "class_session_time": "18:00 - 21:00",
                  "weeks": [
                    {{
                      "week_number": 1,
                      "topic_title": "Introduction to Topic",
                      "learning_objectives": "Key concepts learned this week."
                    }}
                  ]
                }}
              ]
            }}
          ]
        }}

        RAW PDF TEXT:
        {full_extracted_text}
        """

        models_to_try = [
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.1-pro",
            "gemini-3-pro",
            "gemini-3-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-2.5-flash-lite",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-3-deep-think",
            "gemini-2.5-deep-think"
        ]
        
        extracted_programs = []
        last_error = None
        # The catalogue is read on this computer (services/catalogue_reader.py): its pages follow one layout.
        from app.services import catalogue_reader

        try:
            extracted_programs = catalogue_reader.read(contents)
        except Exception as err:
            last_error = err

        # Gemini only for a PDF of another layout, and only when a key was written in .env.
        for model_name in (models_to_try if not extracted_programs and GEMINI_API_KEY else []):
            try:
                raw_json_text = call_gemini_api_direct(prompt, model_name=model_name)
                raw_json_text = raw_json_text.strip()
                if raw_json_text.startswith("```"):
                    raw_json_text = raw_json_text.strip("`").replace("json", "", 1).strip()
                
                data = json.loads(raw_json_text)
                if isinstance(data, dict) and "programs" in data:
                    extracted_programs = data["programs"]
                elif isinstance(data, list):
                    extracted_programs = data
                
                if extracted_programs:
                    break
            except Exception as err:
                last_error = err
                continue

        if not extracted_programs:
            return templates.TemplateResponse(
                request=request,
                name="program_upload.html",
                context={
                    "error": (f"No program could be read: {last_error}" if last_error else
                              "No program could be read: this PDF does not follow the catalogue's layout (a “COURSE PLAN” page per program). "
                              "Add the programs by hand, or write a Gemini key in .env to try another layout."),
                    "results": None
                }
            )

        db = SessionLocal()
        draft_results = []
        
        try:
            freq_online, freq_onsite = get_most_frequent_prices(db)

            for idx, prog_data in enumerate(extracted_programs):
                p_name = str(prog_data.get("name", "")).strip()
                if not p_name:
                    continue

                p_desc = str(prog_data.get("description", "") or "").strip()
                
                existing_prog, match_score = find_semantic_duplicate(db, p_name, p_desc, threshold=0.75)

                raw_p_online = float(prog_data.get("price_online", 0.0) or 0.0)
                raw_p_onsite = float(prog_data.get("price_onsite", 0.0) or 0.0)

                p_online = raw_p_online if raw_p_online > 0 else freq_online
                p_onsite = raw_p_onsite if raw_p_onsite > 0 else freq_onsite

                draft_results.append({
                    "temp_id": idx,
                    "existing_id": existing_prog.id if existing_prog else None,
                    "name": p_name,
                    "description": p_desc,
                    "total_duration_days": prog_data.get("total_duration_days", 60),
                    "price_online": p_online,
                    "price_onsite": p_onsite,
                    "hours_per_week": int(prog_data.get("hours_per_week", 0) or 0),
                    "training_hours": int(prog_data.get("training_hours", 0) or 0),
                    "supervision_hours": int(prog_data.get("supervision_hours", 0) or 0),
                    "class_session_time": str(prog_data.get("class_session_time", "") or ""),
                    "levels": prog_data.get("levels", []),
                    "levels_json": json.dumps(prog_data.get("levels", [])),
                    "is_existing": True if existing_prog else False,
                    "similarity_score": round(match_score, 2)
                })
        finally:
            db.close()

        return templates.TemplateResponse(
            request=request,
            name="program_upload.html",
            context={
                "results": draft_results,
                "filename": file.filename
            }
        )

    except Exception as e:
        return templates.TemplateResponse(
            request=request,
            name="program_upload.html",
            context={
                "error": f"Failed to process program PDF: {str(e)}",
                "results": None
            }
        )


@router.get("/confirm-import")
def confirm_import_get_redirect():
    return RedirectResponse(url="/programs/upload", status_code=303)


@router.post("/confirm-import")
async def confirm_import(request: Request):
    form_data = await request.form()
    selected_indices = form_data.getlist("selected_items")

    if not selected_indices:
        return templates.TemplateResponse(
            request=request,
            name="program_upload.html",
            context={
                "error": "No items were selected for import.",
                "results": None
            }
        )

    db = SessionLocal()
    saved_count = 0
    updated_count = 0

    try:
        for idx in selected_indices:
            name = form_data.get(f"name_{idx}", "").strip()
            if not name:
                continue

            existing_id = form_data.get(f"existing_id_{idx}", "").strip()
            description = form_data.get(f"description_{idx}", "").strip()

            existing_prog = None
            if existing_id:
                existing_prog = db.query(Program).filter(Program.id == existing_id).first()
            if not existing_prog:
                existing_prog, _ = find_semantic_duplicate(db, name, description, threshold=0.75)

            if existing_prog:
                program_obj = existing_prog
                updated_count += 1
                db.query(ProgramLevel).filter(ProgramLevel.program_id == program_obj.id).delete(synchronize_session=False)
                db.flush()
            else:
                program_obj = Program()
                db.add(program_obj)
                saved_count += 1

            program_obj.name = name
            program_obj.description = description
            
            try:
                program_obj.total_duration_days = int(form_data.get(f"total_duration_days_{idx}") or 60)
            except ValueError:
                program_obj.total_duration_days = 60

            try:
                program_obj.price_online = float(form_data.get(f"price_online_{idx}") or 0.0)
            except ValueError:
                program_obj.price_online = 0.0

            try:
                program_obj.price_onsite = float(form_data.get(f"price_onsite_{idx}") or 0.0)
            except ValueError:
                program_obj.price_onsite = 0.0

            try:
                program_obj.hours_per_week = int(form_data.get(f"hours_per_week_{idx}") or 0)
            except ValueError:
                program_obj.hours_per_week = 0

            try:
                program_obj.training_hours = int(form_data.get(f"training_hours_{idx}") or 0)
            except ValueError:
                program_obj.training_hours = 0

            try:
                program_obj.supervision_hours = int(form_data.get(f"supervision_hours_{idx}") or 0)
            except ValueError:
                program_obj.supervision_hours = 0

            program_obj.class_session_time = form_data.get(f"class_session_time_{idx}", "").strip()
            program_obj.is_approved = False
            program_obj.set_embedding(generate_embedding(name, description))

            db.flush()

            raw_levels_json = form_data.get(f"levels_json_{idx}", "[]")
            try:
                levels_data = json.loads(raw_levels_json) if raw_levels_json else []
            except Exception:
                levels_data = []

            for l_idx, lvl in enumerate(levels_data, start=1):
                raw_hpw = lvl.get("hours_per_week")
                parsed_hpw = int(raw_hpw) if raw_hpw is not None and str(raw_hpw).isdigit() else None

                level_obj = ProgramLevel(
                    program_id=program_obj.id,
                    level_order=lvl.get("level_order", l_idx),
                    title=str(lvl.get("title", f"Level {l_idx}")),
                    description=str(lvl.get("description", "")),
                    hours_per_week=parsed_hpw,
                    class_session_time=lvl.get("class_session_time")
                )
                db.add(level_obj)
                db.flush()

                weeks_data = lvl.get("weeks", [])
                for w_idx, wk in enumerate(weeks_data, start=1):
                    raw_wn = wk.get("week_number")
                    parsed_wn = int(raw_wn) if raw_wn is not None and str(raw_wn).isdigit() else w_idx

                    week_obj = WeeklyCurriculum(
                        level_id=level_obj.id,
                        week_number=parsed_wn,
                        topic_title=str(wk.get("topic_title", f"Week {w_idx}")),
                        learning_objectives=str(wk.get("learning_objectives", ""))
                    )
                    db.add(week_obj)

            if Notification is not None:
                notif = Notification(
                    recipient_role="admin",
                    category="PROGRAM_APPROVAL",
                    title="Program Approval Pending",
                    message=f"Program '{program_obj.name}' pending approval. Online: {program_obj.price_online:.2f} DT, Onsite: {program_obj.price_onsite:.2f} DT.",
                    action_url=f"/programs/{program_obj.id}",
                    program_id=program_obj.id,
                    is_read=False
                )
                db.add(notif)
        db.commit()
    except Exception as db_err:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="program_upload.html",
            context={
                "error": f"Failed to save changes: {str(db_err)}",
                "results": None
            }
        )
    finally:
        db.close()

    return templates.TemplateResponse(
        request=request,
        name="program_upload.html",
        context={
            "success_msg": f"Successfully processed items: {saved_count} created, {updated_count} updated. Sent for Admin Approval.",
            "results": None
        }
    )


@router.post("/create")
def create_program_manual(
    name: str = Form(...),
    total_duration_days: int = Form(60),
    price_online: float = Form(0.0),
    price_onsite: float = Form(0.0),
    hours_per_week: int = Form(0),
    training_hours: int = Form(0),
    supervision_hours: int = Form(0),
    class_session_time: str = Form(""),
    description: str = Form("")
):
    db = SessionLocal()
    try:
        duplicate, score = find_semantic_duplicate(db, name, description, threshold=0.75)
        
        if duplicate:
            if not duplicate.description and description:
                duplicate.description = description
            if (not duplicate.total_duration_days or duplicate.total_duration_days == 0) and total_duration_days:
                duplicate.total_duration_days = total_duration_days
            db.commit()
            return RedirectResponse(url=f"/programs/{duplicate.id}", status_code=303)

        freq_online, freq_onsite = get_most_frequent_prices(db)

        final_price_online = price_online if price_online > 0 else freq_online
        final_price_onsite = price_onsite if price_onsite > 0 else freq_onsite

        new_program = Program(
            name=name,
            total_duration_days=total_duration_days,
            price_online=final_price_online,
            price_onsite=final_price_onsite,
            hours_per_week=hours_per_week,
            training_hours=training_hours,
            supervision_hours=supervision_hours,
            class_session_time=class_session_time,
            description=description,
            is_approved=False
        )
        new_program.set_embedding(generate_embedding(name, description))
        
        db.add(new_program)
        db.flush()

        if Notification is not None:
            notif = Notification(
                type="Program Approval Pending",
                details=f"Program '{new_program.name}' submitted. Prices auto-assigned (Online: {final_price_online:.2f} DT, Onsite: {final_price_onsite:.2f} DT).",
                program_id=new_program.id,
                status="unread"
            )
            db.add(notif)
        
        db.commit()
    finally:
        db.close()
    return RedirectResponse(url="/programs/all", status_code=303)


# ==========================================
# HYBRID DEDUPLICATION & STREAMING FUSION
# ==========================================

@router.get("/fuse-duplicates-stream")
async def fuse_duplicates_stream(threshold: float = 0.75):
    """
    Streams duplicate candidate pairs using hybrid matching over SSE.
    Emits source (candidate to remove) and target (candidate to keep) payload structures.
    """
    async def log_generator():
        db = SessionLocal()
        try:
            all_programs = db.query(Program).all()
            seen_pairs = set()

            for i in range(len(all_programs)):
                current = all_programs[i]

                for j in range(i + 1, len(all_programs)):
                    target_prog = all_programs[j]
                    
                    pair_key = tuple(sorted([str(current.id), str(target_prog.id)]))
                    if pair_key in seen_pairs:
                        continue

                    score = compute_hybrid_similarity(
                        current.name, current.description or "",
                        target_prog.name, target_prog.description or ""
                    )

                    if score >= threshold:
                        seen_pairs.add(pair_key)
                        
                        # Set record with more detailed content as target (KEEP)
                        if (not current.description and target_prog.description) or (current.total_duration_days == 0 and target_prog.total_duration_days > 0):
                            target, source = target_prog, current
                        else:
                            target, source = current, target_prog

                        payload = {
                            "similarity": score,
                            "target": {
                                "id": str(target.id),
                                "name": target.name,
                                "description": target.description or "",
                                "price": float(getattr(target, "price_online", 0.0) or 0.0),
                                "duration": int(getattr(target, "total_duration_days", 0) or 0)
                            },
                            "source": {
                                "id": str(source.id),
                                "name": source.name,
                                "description": source.description or "",
                                "price": float(getattr(source, "price_online", 0.0) or 0.0),
                                "duration": int(getattr(source, "total_duration_days", 0) or 0)
                            }
                        }

                        yield f"data: {json.dumps(payload)}\n\n"
                        await asyncio.sleep(0.05)
        except Exception as err:
            yield f"data: {json.dumps({'error': str(err)})}\n\n"
        finally:
            db.close()

    return StreamingResponse(
        log_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/{source_id}/fuse")
def execute_single_fusion(
    source_id: str,
    target_program_id: str = Form(...),
    delete_source: bool = Form(True),
    db: Session = Depends(get_db)
):
    """Fuses a single source program into a target program and removes the source record."""
    source_prog = db.query(Program).filter(Program.id == source_id).first()
    target_prog = db.query(Program).filter(Program.id == target_program_id).first()

    if not source_prog or not target_prog:
        raise HTTPException(status_code=404, detail="Source or Target program not found")

    merge_program_records(db, target_prog, source_prog)
    db.commit()

    return {"status": "success", "message": f"Program '{source_id}' successfully fused into '{target_program_id}'."}


@router.post("/fuse-duplicates")
def trigger_fuse_duplicates(threshold: float = 0.75):
    """Scans and automatically fuses all duplicate programs in the database in bulk."""
    db = SessionLocal()
    try:
        all_programs = db.query(Program).all()
        merged_count = 0
        fused_pairs = []

        i = 0
        while i < len(all_programs):
            current = all_programs[i]

            j = i + 1
            while j < len(all_programs):
                target = all_programs[j]

                score = compute_hybrid_similarity(
                    current.name, current.description or "",
                    target.name, target.description or ""
                )

                if score >= threshold:
                    if (not current.description and target.description) or (current.total_duration_days == 0 and target.total_duration_days > 0):
                        primary, duplicate = target, current
                    else:
                        primary, duplicate = current, target

                    fused_pairs.append({
                        "primary": primary.name,
                        "merged": duplicate.name,
                        "similarity": score
                    })
                    
                    merge_program_records(db, primary, duplicate)
                    all_programs.remove(duplicate)
                    merged_count += 1
                    continue
                j += 1
            i += 1
        
        db.commit()
        return JSONResponse(content={
            "status": "success",
            "merged_count": merged_count,
            "fused_pairs": fused_pairs
        })
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.post("/{program_id}/approve")
def approve_program(program_id: str):
    db = SessionLocal()
    try:
        program = db.query(Program).filter(Program.id == program_id).first()
        if not program:
            raise HTTPException(status_code=404, detail="Program not found")
        
        program.is_approved = True

        if ActivityLog is not None:
            log = ActivityLog(
                event_type="Course Approved",
                details=f"Admin approved program: '{program.name}' (Online: {program.price_online:.2f} DT, Onsite: {program.price_onsite:.2f} DT)."
            )
            db.add(log)

        db.commit()
        return {"status": "success", "message": f"Program '{program.name}' approved successfully."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


_LEVEL_PREFIX = re.compile(r"^\s*(level|niveau)\s*\d+\s*[:\-–.]\s*", re.IGNORECASE)


def _web_link(value) -> str | None:
    """An address read from the website, kept only when it is a web link."""
    value = str(value or "").strip()
    return value if value.lower().startswith(("https://", "http://")) else None


def program_story(db: Session, program: Program) -> dict:
    """Everything the page of one program tells: its journey week by week (with
    the website's videos), who follows it, its classes and who can teach it."""
    formation = (
        db.query(WebsiteItem)
        .filter(WebsiteItem.kind == "formation", WebsiteItem.program_id == program.id, WebsiteItem.gone_at.is_(None))
        .first()
    )
    site = formation.details if formation else {}
    videos = {}
    for week in site.get("weeks") or []:
        if isinstance(week, dict) and str(week.get("week", "")).isdigit() and _web_link(week.get("video_url")):
            videos[int(week["week"])] = {
                "title": str(week.get("video_title") or "Video of the week")[:120],
                "url": _web_link(week.get("video_url")),
                "length": str(week.get("video_length") or "")[:12],
                "free": bool(week.get("free")),
            }

    journey, number = [], 0
    for level in program.levels:
        weeks = []
        for week in level.weeks:
            number += 1
            weeks.append({"number": number, "week": week, "video": videos.get(number)})
        journey.append({
            "level": level,
            "title": _LEVEL_PREFIX.sub("", level.title or "").strip() or f"Level {level.level_order or len(journey) + 1}",
            "weeks": weeks,
        })

    now = local_now()
    order = {ClassStatus.ONGOING: 0, ClassStatus.NOT_STARTED: 1, ClassStatus.COMPLETED: 2}
    classes = sorted(
        db.query(ClassGroup).filter(ClassGroup.program_id == program.id).all(),
        key=lambda item: (order.get(item.status, 3), item.start_date or datetime.max),
    )
    registrations = [
        registration for registration in db.query(Registration).filter(Registration.program_id == program.id).all()
        if registration.status != RegistrationStatus.CANCELLED
    ]
    paid = sum(1 for registration in registrations if registration.payment_status == PaymentStatus.PAID)
    subject = level_bank.subject_of(program.name, program.website_slug)
    prices = site.get("price") if isinstance(site.get("price"), dict) else {}
    return {
        "site": {
            "url": _web_link(formation.url) if formation else None,
            "level": str(site.get("level") or "")[:60],
            "mode": str(site.get("mode") or "")[:40],
            "duration": str(site.get("duration") or "")[:40],
            "certificate": bool(site.get("certificate")),
            "recordings": str(prices.get("recordings") or "")[:40],
            "technologies": [str(item)[:24] for item in (site.get("technologies") or []) if isinstance(item, str)][:8],
        },
        "journey": journey,
        "weeks": number,
        "videos": len([1 for level in journey for week in level["weeks"] if week["video"]]),
        "classes": [
            {"cls": item, "look": class_looks.look(item), "summary": class_summary(item, now)} for item in classes
        ],
        "people": {"total": len(registrations), "paid": paid,
                   "pct": round(paid / len(registrations) * 100) if registrations else 0},
        "teachers": teachers_of(db, program),
        "level_form": {
            "topics": len(level_check.topics_of(db, program)),
            "checks": len(level_bank.SUBJECTS[subject][1]) if subject else 0,
        },
    }


@router.get("/{program_id}")
def program_detail(program_id: str, request: Request):
    db = SessionLocal()
    try:
        program = db.query(Program).filter(Program.id == program_id).first()
        programs = db.query(Program).order_by(Program.name.asc()).all()
        if not program:
            raise HTTPException(status_code=404, detail="Program not found")
        position = next((index for index, item in enumerate(programs) if item.id == program.id), 0)
        return templates.TemplateResponse(
            request=request,
            name="program_details.html",
            context={
                "active_page": "courses",
                "program": program,
                "programs": programs,
                "others": [{"program": item, "icon": program_icons.view(item)} for item in programs if item.id != program.id],
                "previous": programs[position - 1] if position > 0 else None,
                "following": programs[position + 1] if position + 1 < len(programs) else None,
                "icon": program_icons.view(program),
                "cover": program_icons.cover(position),
                "missing": program_assistant.missing_labels(program),
                "proposal": program_assistant.open_proposal(db, program.id),
                "story": program_story(db, program),
                "reviews": __import__("app.services.program_feedback", fromlist=["of_program"]).of_program(db, program.id),
                "market": market.analysis(db, program),
                "reco": __import__("app.services.recommender", fromlist=["recommend"]).recommend(db, program),
                "notice": request.query_params.get("notice"),
                "error": request.query_params.get("error"),
            }
        )
    finally:
        db.close()


@router.post("/{program_id}/delete")
def delete_program(program_id: str):
    db = SessionLocal()
    try:
        program = db.query(Program).filter(Program.id == program_id).first()
        if program:
            icon = program_icons.path_of(program)
            db.delete(program)
            db.commit()
            if icon is not None:
                icon.unlink(missing_ok=True)
    finally:
        db.close()
    return RedirectResponse(url="/programs/all", status_code=303)