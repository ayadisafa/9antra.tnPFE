"""What the email assistant knows, and how it finds the relevant part (the
retrieval half of RAG).

The knowledge base has two sources:

* `system` entries, rebuilt automatically from programs, curricula, open
  classes and the class settings, so answers follow the live data;
* `admin` / `learned` entries: questions and answers written by the admin, or
  saved from a reply the admin gave to a candidate.

Retrieval is hybrid: sentence-embedding similarity plus word overlap. The
word overlap maps French, English and Tunisian wording of the same idea
("prix", "price", "9addech") to one concept, because candidates write in
French while much of the program data is in English.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata

from sqlalchemy.orm import Session, joinedload

from app.models.classgroup import ClassGroup, ClassStatus
from app.models.knowledge import KnowledgeEntry
from app.models.program import Program, ProgramLevel
from app.models.registration import Registration, RegistrationStatus
from app.services.email_threads import utcnow
from app.services.schedule_utils import class_start_dt, describe_schedule_fr, format_date_fr
from app.services.settings_service import get_settings, parse_reminder_days

logger = logging.getLogger(__name__)

SEMANTIC_WEIGHT = 0.55
FRESH_SECONDS = 300

_sync_lock = threading.Lock()
_last_sync = 0.0

# Different words candidates use for the same idea -> one shared token.
_CONCEPTS = {
    "_price": "prix tarif tarifs cout couts combien price prices cost costs fee fees frais montant "
              "9addech 9adech kaddech kadech gadech 9adeh b9adech قداش بقداش سوم",
    "_schedule": "horaire horaires heure heures time times schedule timing quand when wa9tech wa9teh "
                 "wakteh waqtech وقتاش jour jours day days",
    "_start": "debut commence commencer commencent commencera demarre demarrer demarrage start starts "
              "starting begin begins yabda tabda nebda يبدا تبدا",
    "_duration": "duree semaine semaines week weeks mois month months long longtemps duration dure durent",
    "_trainer": "prof professeur professeurs formateur formatrice formateurs enseignant trainer trainers "
                "teacher teachers instructor instructeur",
    "_payment": "paiement paiements payer paye payment payments pay paid versement tranche tranches "
                "facilite facilites installment installments khlas nkhales خلاص نخلص",
    "_certificate": "certificat certificats certification attestation diplome certificate certificates",
    "_online": "ligne online distance remote distanciel visio",
    "_onsite": "presentiel onsite",
    "_location": "adresse address location lieu emplacement local locaux situe situes situee located "
                 "trouve trouvez itineraire parking localisation localisations localization localizations "
                 "maps directions",
    "_curriculum": "programme contenu contenus syllabus curriculum plan chapitre chapitres module modules "
                   "topics sujets matiere matieres apprendre learn",
    "_class": "classe classes groupe groupes class group groups seance seances session sessions",
    "_refund": "remboursement rembourser rembourse refund refunds annulation annuler annule cancel cancellation",
    "_level": "niveau niveaux level levels prerequis prerequisite prerequisites debutant beginner avance",
    "_register": "inscription inscriptions inscrire inscrit register registration enroll enrollment",
}
_CONCEPT_OF = {word: concept for concept, words in _CONCEPTS.items() for word in words.split()}

_STOPWORDS = set(
    "le la les un une des de du d et ou a au aux en pour par sur dans avec sans ce cette ces mon ma mes "
    "votre vos notre nos je tu il elle nous vous ils elles est sont suis ai as avez ont que qui quoi dont "
    "ne pas plus tres bien bonjour bonsoir salut merci svp stp cordialement est-ce si mais donc car j l qu "
    "n s c m t y se sa son ses leur leurs etre avoir fait faire peut peux pouvez voudrais veux savoir "
    "the an of to in on for and or is are am be i you he she we they my your our this that it do does can "
    "could would please hello hi thanks thank regards me about what which how there any some have has "
    # What people write around a question, which says nothing of what they ask.
    "9antra question questions demande renseignement renseignements information informations info infos "
    "exactement exactly exact exacte juste just want need like voulais aimerais souhaite "
    "where comment pourquoi why quel quelle quels quelles who".split()
)
_TOKEN = re.compile(r"[a-z0-9؀-ۿ]+")


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def tokenize(text: str | None) -> set[str]:
    tokens = set()
    for raw in _TOKEN.findall(_fold(text or "")):
        if raw in _STOPWORDS or (len(raw) < 2 and not raw.isdigit()):
            continue
        token = _CONCEPT_OF.get(raw)
        if token is None:
            singular = raw[:-1] if len(raw) > 3 and raw.endswith("s") else raw
            token = _CONCEPT_OF.get(singular, singular)
        tokens.add(token)
    return tokens


# ------------------------------------------------------------------
# Embeddings
# ------------------------------------------------------------------

def _embed(texts: list[str]) -> list[list[float]] | None:
    """Sentence embeddings, or None when the model is unavailable (word overlap still works)."""
    if os.getenv("RAG_EMBEDDINGS", "1") == "0" or not texts:
        return None
    try:
        from app.services.embedding_service import get_similarity_model

        vectors = get_similarity_model().encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [[round(float(value), 5) for value in vector] for vector in vectors]
    except Exception:
        logger.exception("Embedding model unavailable; knowledge search falls back to word overlap.")
        return None


# Tests replace this to avoid loading the model.
embed = _embed


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _embedding_text(title: str, content: str) -> str:
    return f"{title}\n{content}"[:1200]


def _entry_embedding_text(entry: KnowledgeEntry) -> str:
    # An answer the admin taught is matched on the question it answers. Matching
    # on the words of the answer sent it to unrelated questions that happened to
    # share a word with it.
    if entry.origin != "system":
        return entry.title
    return _embedding_text(entry.title, entry.content)


def _hash(title: str, content: str) -> str:
    return hashlib.sha1(f"{title}\n{content}".encode("utf-8")).hexdigest()


# ------------------------------------------------------------------
# Is this the question the admin already answered?
# ------------------------------------------------------------------
#
# Measured on 2026-10-13 with the sentence model above (it only really reads
# English): the same question in other words scored a median 0.26, while two
# different French questions about one formation scored up to 0.92. So whether
# a new question *is* a taught one is decided on its words, both ways round:
# what the candidate asks must be in the taught wording, and the reverse.

MAX_WORDINGS = 14
SAME_QUESTION = 1.0         # `carries` from which a taught answer is given without asking
ALIKE_QUESTION = 0.45       # `sameness` from which it is offered to the admin as a draft
# Words every question here may or may not have ("… à la fin de la formation ?").
_GENERIC = {"formation", "cour", "cours", "course", "training"}


def _weight(token: str) -> int:
    # A subject ("_price") or a figure tells two questions apart; an ordinary word less.
    return 3 if token.startswith("_") or any(char.isdigit() for char in token) else 1


def _core(tokens: set[str]) -> set[str]:
    """What a question is made of: its words without the ones every question
    may have, a long word cut to its beginning so that "acceptés" and
    "acceptez-vous" are one word."""
    return {
        token[:6] if len(token) >= 7 and token.isalpha() else token
        for token in tokens - _GENERIC
    }


def sameness(left: set[str], right: set[str]) -> float:
    """0..1: how much two questions look alike, by their words."""
    left, right = _core(left), _core(right)
    if not left or not right:
        return 0.0
    shared = sum(_weight(token) for token in left & right)
    return round(min(shared / sum(map(_weight, left)), shared / sum(map(_weight, right))), 3)


def carries(asked: set[str], known: set[str]) -> float:
    """1 when a question is a known wording, word for word once the small
    words are gone (in any order); 0 otherwise. One word more or less makes
    another question: "par carte" and "par chèque", "samedi" and "dimanche",
    "le prix" and "le prix du stage" must not get the same answer by itself."""
    asked, known = _core(asked), _core(known)
    return 1.0 if asked and asked == known else 0.0


def question_key(text: str | None) -> str:
    """Two wordings with the same key are the same question."""
    words = sorted(_core(tokenize(text)))
    return " ".join(words) if words else " ".join(_fold(text or "").split())


def wordings_of(entry: KnowledgeEntry) -> list[dict]:
    """Every way a taught question is known: {"q", "sure"}. The admin's own
    wording and the ones they confirmed are sure; the others are the system's guesses."""
    return [{"q": entry.title, "sure": True}] + [
        {"q": variant["q"], "sure": variant.get("by") != "system"} for variant in entry.get_variants()
    ]


def add_wording(entry: KnowledgeEntry, question: str | None, *, sure: bool = True) -> bool:
    """Remember another way the same question is asked. True when it was new.

    A guess the admin then confirms becomes sure.
    """
    question = " ".join((question or "").split())[:300]
    key = question_key(question)
    if not key or key == question_key(entry.title):
        return False
    variants = entry.get_variants()
    for variant in variants:
        if question_key(variant["q"]) == key:
            if sure and variant.get("by") == "system":
                variant["by"] = "admin"
                entry.variants = json.dumps(variants, ensure_ascii=False)
                return True
            return False
    if len(variants) >= MAX_WORDINGS:
        # Guesses make room for what real candidates wrote.
        guesses = [variant for variant in variants if variant.get("by") == "system"]
        if not sure or not guesses:
            return False
        variants.remove(guesses[-1])
    variants.append({"q": question, "by": "admin" if sure else "system"})
    entry.variants = json.dumps(variants, ensure_ascii=False)
    return True


def same_question(db: Session, question: str | None, *, skip_id: str | None = None) -> KnowledgeEntry | None:
    """The taught entry that already holds this very question, if any."""
    key = question_key(question)
    if not key:
        return None
    for entry in db.query(KnowledgeEntry).filter(KnowledgeEntry.origin != "system").all():
        if entry.id != skip_id and any(question_key(item["q"]) == key for item in wordings_of(entry) if item["sure"]):
            return entry
    return None


# ------------------------------------------------------------------
# System knowledge, generated from live data
# ------------------------------------------------------------------

def _money(value) -> str:
    return f"{float(value or 0):.0f} DT"


def _program_weeks(program: Program) -> int:
    return sum(len(level.weeks) for level in program.levels)


def _class_line(db: Session, class_group: ClassGroup) -> str:
    start = class_start_dt(class_group)
    enrolled = (
        db.query(Registration)
        .filter(
            Registration.class_group_id == class_group.id,
            Registration.status != RegistrationStatus.CANCELLED,
        )
        .count()
    )
    capacity = class_group.max_capacity or class_group.capacity or 0
    if start:
        when = f"démarre le {format_date_fr(start)}"
        if class_group.start_time:
            when += f" à {class_group.start_time:%H:%M}"
    else:
        when = (
            f"démarre dès que {class_group.min_students_to_start or 5} candidats sont inscrits "
            f"(la date exacte est envoyée par email)"
        )
    seats = f"{enrolled}/{capacity} inscrits" if capacity else f"{enrolled} inscrit(s)"
    if capacity and enrolled < capacity:
        seats += f", il reste {capacity - enrolled} place(s)"
    trainer = class_group.professor_name or "pas encore désigné"
    return (
        f"Classe {class_group.name} : {when}. Horaire (schedule) : {describe_schedule_fr(class_group)}. "
        f"Durée (duration) : {class_group.total_weeks or 12} semaines. Formateur (trainer) : {trainer}. "
        f"Places : {seats}."
    )


def _system_chunks(db: Session) -> list[dict]:
    settings = get_settings(db)
    programs = (
        db.query(Program)
        .options(joinedload(Program.levels).joinedload(ProgramLevel.weeks))
        .order_by(Program.name)
        .all()
    )
    open_classes = (
        db.query(ClassGroup)
        .filter(ClassGroup.status.in_([ClassStatus.NOT_STARTED, ClassStatus.ONGOING]))
        .all()
    )
    classes_by_program: dict[str, list[ClassGroup]] = {}
    for class_group in open_classes:
        classes_by_program.setdefault(class_group.program_id, []).append(class_group)

    chunks: list[dict] = []
    for program in programs:
        weeks = _program_weeks(program)
        lines = [f"Formation (program) : {program.name}."]
        if program.description:
            lines.append(program.description.strip())
        lines.append(
            f"Prix (price) : {_money(program.price_online)} en ligne (online, à distance), "
            f"{_money(program.price_onsite)} en présentiel (onsite)."
        )
        if weeks:
            duration = f"Durée (duration) : {weeks} semaines (weeks)"
            if program.total_duration_days:
                duration += f", soit environ {program.total_duration_days} jours"
            lines.append(duration + ".")
        if program.hours_per_week:
            pace = f"Rythme : {program.hours_per_week} heures par semaine (hours per week)"
            if program.training_hours:
                pace += f", dont {program.training_hours} h de formation"
            if program.supervision_hours:
                pace += f" et {program.supervision_hours} h d'encadrement"
            lines.append(pace + ".")
        if program.class_session_time:
            lines.append(f"Horaire habituel des séances (usual session schedule) : {program.class_session_time}.")
        if program.levels:
            lines.append("Niveaux (levels) : " + ", ".join(level.title for level in program.levels) + ".")
        not_started = [item for item in classes_by_program.get(program.id, []) if item.status == ClassStatus.NOT_STARTED]
        if not_started:
            lines.append("Prochaines classes (next classes) :")
            lines += [_class_line(db, class_group) for class_group in not_started]
        else:
            lines.append(
                "Aucune classe ouverte pour le moment : une classe est créée automatiquement dès qu'un candidat paie."
            )
        chunks.append({
            "source_key": f"program:{program.id}",
            "kind": "program",
            "program_id": program.id,
            "title": f"{program.name} — présentation, prix et horaires",
            "content": "\n".join(lines),
        })

        for level in program.levels:
            if not level.weeks:
                continue
            week_lines = []
            for week in level.weeks:
                line = f"Semaine {week.week_number} (week {week.week_number}) : {week.topic_title}"
                if week.learning_objectives:
                    objectives = " ".join(week.learning_objectives.split())
                    line += " — " + (objectives if len(objectives) <= 220 else objectives[:219] + "…")
                week_lines.append(line)
            chunks.append({
                "source_key": f"program:{program.id}:level:{level.id}",
                "kind": "curriculum",
                "program_id": program.id,
                "title": f"{program.name} — contenu du programme ({level.title})",
                "content": (
                    f"Contenu (curriculum, syllabus) de la formation {program.name}, {level.title}, "
                    f"{len(level.weeks)} semaines :\n" + "\n".join(week_lines)
                ),
            })

    if programs:
        catalogue = "; ".join(
            f"{program.name} ({_money(program.price_online)} en ligne, {_money(program.price_onsite)} en présentiel)"
            for program in programs
        )
        chunks.append({
            "source_key": "system:catalogue",
            "kind": "process",
            "program_id": None,
            "title": "Formations proposées par 9antra et leurs prix",
            "content": f"Formations (programs, courses) proposées par 9antra : {catalogue}.",
        })

    reminder_days = parse_reminder_days(settings.training_reminder_days)
    reminders = ", ".join(str(day) for day in reminder_days) or "7, 3, 1"
    if settings.class_start_mode == "min_enrollment":
        start_rule = (
            f"Une nouvelle classe démarre lorsqu'au moins {settings.min_students_to_start} candidats sont inscrits ; "
            f"la date est alors fixée environ {settings.min_start_notice_days} jours plus tard et envoyée par email."
        )
    else:
        start_rule = (
            f"Une nouvelle classe démarre en général {settings.default_class_start_days} jours après sa création, "
            f"le premier jour de séance."
        )
    chunks.append({
        "source_key": "system:enrollment",
        "kind": "process",
        "program_id": None,
        "title": "Inscription, paiement, affectation à une classe et rappels",
        "content": (
            "Comment se passe l'inscription (enrollment process) : après le paiement complet, le candidat est "
            "affecté automatiquement à une classe de sa formation qui n'a pas encore démarré ; s'il n'y en a pas, "
            "une nouvelle classe est créée. Il reçoit alors un email de confirmation avec la classe, la date de "
            "démarrage, l'horaire et le formateur. Tant que le paiement n'est pas complet, l'inscription n'est pas "
            f"affectée à une classe. {start_rule} Des rappels (reminders) sont envoyés par email {reminders} jours "
            "avant la première séance, avec la date et l'heure exactes. Si le créneau ne convient pas, le candidat "
            "peut répondre à l'email et l'équipe lui propose une autre classe."
        ),
    })
    # What the public website and the Facebook page publish (see website_sync).
    from app.services import website_sync

    chunks += website_sync.knowledge_chunks(db)
    return chunks


def sync_knowledge(db: Session) -> dict:
    """Bring the system knowledge in line with the live data; embed what changed."""
    global _last_sync
    with _sync_lock:
        wanted = {chunk["source_key"]: chunk for chunk in _system_chunks(db)}
        existing = {
            entry.source_key: entry
            for entry in db.query(KnowledgeEntry).filter(KnowledgeEntry.origin == "system").all()
        }
        changed: list[KnowledgeEntry] = []
        created = removed = 0

        for key, chunk in wanted.items():
            digest = _hash(chunk["title"], chunk["content"])
            entry = existing.get(key)
            if entry is None:
                entry = KnowledgeEntry(origin="system", source_key=key)
                db.add(entry)
                created += 1
            elif entry.content_hash == digest and entry.is_active:
                continue
            entry.kind = chunk["kind"]
            entry.program_id = chunk["program_id"]
            entry.title = chunk["title"]
            entry.content = chunk["content"]
            entry.content_hash = digest
            entry.is_active = True
            changed.append(entry)

        for key, entry in existing.items():
            if key not in wanted:
                db.delete(entry)
                removed += 1

        # Admin-written entries are embedded here too if that failed earlier.
        changed += [
            entry for entry in db.query(KnowledgeEntry)
            .filter(KnowledgeEntry.origin != "system", KnowledgeEntry.embedding.is_(None))
            .all()
        ]
        if changed:
            vectors = embed([_entry_embedding_text(entry) for entry in changed])
            if vectors:
                for entry, vector in zip(changed, vectors):
                    entry.embedding = json.dumps(vector)
        if changed or removed:
            db.commit()
        _last_sync = time.monotonic()
        return {"created": created, "updated": len(changed) - created, "removed": removed}


def ensure_fresh(db: Session) -> None:
    """Re-sync before answering if the last sync is more than a few minutes old."""
    if time.monotonic() - _last_sync > FRESH_SECONDS or _last_sync == 0.0:
        try:
            sync_knowledge(db)
        except Exception:
            db.rollback()
            logger.exception("Knowledge sync failed; answering from the previous index.")


# ------------------------------------------------------------------
# Admin-written and learned knowledge
# ------------------------------------------------------------------

def save_entry(db: Session, *, title: str, content: str, entry_id: str | None = None,
               origin: str = "admin", source_email_id: str | None = None) -> KnowledgeEntry:
    """Teach an answer. A question that is already taught gets its answer
    replaced instead of being stored a second time."""
    title = " ".join((title or "").split())
    content = (content or "").strip()
    if not title or not content:
        raise ValueError("Both the question and the answer are required.")
    entry = db.get(KnowledgeEntry, entry_id) if entry_id else None
    if entry is not None and entry.origin == "system":
        raise ValueError("This entry is generated from the system's data and cannot be edited here.")
    if entry is None:
        entry = same_question(db, title)
    if entry is None:
        entry = KnowledgeEntry(kind="faq", origin=origin, source_email_id=source_email_id)
        db.add(entry)
    elif not entry_id and question_key(entry.title) != question_key(title):
        # Known under another wording: the entry keeps its own question.
        title = entry.title
    if entry.title and question_key(entry.title) != question_key(title):
        # The question itself was rewritten: the guesses made for the old one go.
        kept = [variant for variant in entry.get_variants() if variant.get("by") != "system"]
        entry.variants = json.dumps(kept, ensure_ascii=False) if kept else None
        entry.wordings_at = None
    entry.title = title[:300]
    entry.content = content
    entry.content_hash = _hash(entry.title, entry.content)
    entry.is_active = True
    vectors = embed([_entry_embedding_text(entry)])
    entry.embedding = json.dumps(vectors[0]) if vectors else None
    db.flush()
    return entry


def mark_used(db: Session, entry_ids: list[str]) -> None:
    if not entry_ids:
        return
    for entry in db.query(KnowledgeEntry).filter(KnowledgeEntry.id.in_(entry_ids)).all():
        entry.use_count = (entry.use_count or 0) + 1
        entry.last_used_at = utcnow()


def stats(db: Session) -> dict:
    rows = db.query(KnowledgeEntry.origin, KnowledgeEntry.kind).filter(KnowledgeEntry.is_active.is_(True)).all()
    return {
        "total": len(rows),
        "website": db.query(KnowledgeEntry).filter(
            KnowledgeEntry.is_active.is_(True), KnowledgeEntry.source_key.like("website:%"),
        ).count(),
        "system": sum(1 for origin, _ in rows if origin == "system"),
        "admin": sum(1 for origin, _ in rows if origin == "admin"),
        "learned": sum(1 for origin, _ in rows if origin == "learned"),
        "programs": sum(1 for origin, kind in rows if kind == "program"),
    }


# ------------------------------------------------------------------
# Search
# ------------------------------------------------------------------

def search(db: Session, query: str, *, extra_queries: list[str] | None = None,
           program_ids: list[str] | None = None, limit: int = 5,
           questions: list[str] | None = None) -> list[dict]:
    """The knowledge most relevant to a question, best first.

    `questions` is the question itself, in one or several cuts (with or
    without the subject line); without it the query is taken as the question.

    Each hit has `quality` (0..1, the one figure to judge it by) and `score`
    with its `semantic` and `lexical` parts, so the caller can show the admin
    why the assistant was or was not confident.

    The system's own knowledge is scored on meaning and words together. A
    taught answer is scored on its question only, against each wording it is
    known under: `same` says the candidate asks that very question (sure
    wordings only), `quality` how much it looks like one of them. `related`
    additionally counts the words of the answer, and is only good enough to
    suggest a draft.
    """
    queries = [text for text in [query, *(extra_queries or [])] if text and text.strip()]
    entries = db.query(KnowledgeEntry).filter(KnowledgeEntry.is_active.is_(True)).all()
    if not queries or not entries:
        return []

    full_tokens = {entry.id: tokenize(f"{entry.title} {entry.content}") for entry in entries}
    doc_tokens = {
        entry.id: full_tokens[entry.id] if entry.origin == "system" else tokenize(entry.title)
        for entry in entries
    }
    # What a taught wording is compared with: the question as the candidate put it.
    asked = [tokens for tokens in (tokenize(text) for text in (questions or queries[:1])) if tokens]
    frequency: dict[str, int] = {}
    for tokens in doc_tokens.values():
        for token in tokens:
            frequency[token] = frequency.get(token, 0) + 1
    total = len(entries)

    def idf(token: str) -> float:
        return math.log(1 + total / (1 + frequency.get(token, 0)))

    query_tokens = [tokenize(text) for text in queries]
    vectors = embed(queries)
    program_ids = set(program_ids or [])

    def overlap(tokens: set[str]) -> float:
        best = 0.0
        for wanted in query_tokens:
            weight = sum(idf(token) for token in wanted)
            if weight:
                best = max(best, sum(idf(token) for token in wanted & tokens) / weight)
        return best

    hits = []
    for entry in entries:
        lexical = overlap(doc_tokens[entry.id])

        semantic = None
        vector = entry.get_embedding() if vectors else None
        if vector:
            best = max(_cosine(query_vector, vector) for query_vector in vectors)
            semantic = max(0.0, min(1.0, (best - 0.15) / 0.5))

        def blend(lexical_part: float) -> float:
            if semantic is None:
                return lexical_part
            return SEMANTIC_WEIGHT * semantic + (1 - SEMANTIC_WEIGHT) * lexical_part

        score = blend(lexical)
        related = score if entry.origin == "system" else blend(overlap(full_tokens[entry.id]))
        if entry.program_id and entry.program_id in program_ids:
            # The candidate's own program is the most likely subject.
            score = min(1.0, score * 1.12 + 0.03)
        hit = {
            "id": entry.id,
            "kind": entry.kind,
            "origin": entry.origin,
            "title": entry.title,
            "content": entry.content,
            "score": round(score, 3),
            "related": round(related, 3),
            "semantic": None if semantic is None else round(semantic, 3),
            "lexical": round(lexical, 3),
        }
        if entry.origin == "system":
            hit["quality"] = quality_of(score)
        else:
            same, quality, wording = 0.0, 0.0, entry.title
            for item in wordings_of(entry):
                known = tokenize(item["q"])
                if item["sure"]:
                    same = max([same] + [carries(tokens, known) for tokens in asked])
                close = max((sameness(tokens, known) for tokens in asked), default=0.0)
                if close > quality:
                    quality, wording = close, item["q"]
            # On the same scale as the system's knowledge, so one list can be sorted.
            hit.update(same=same, wording=wording, quality=quality, score=round(0.2 + 0.5 * quality, 3))
        hits.append(hit)

    # At equal quality what the admin taught comes before what the system worked out.
    hits.sort(key=lambda hit: (hit["quality"], hit["origin"] != "system", hit["score"]), reverse=True)
    return hits[:limit]


def quality_of(score: float) -> float:
    """Retrieval score of the system's knowledge -> 0..1 quality. Measured on
    real questions: subjects the system knows score 0.55 and above (70% here),
    subjects it does not know stay under 0.45 (below the 50% "knows" line)."""
    return round(max(0.0, min(1.0, (score - 0.20) / 0.5)), 3)


def tidy_taught(db: Session) -> int:
    """One entry per taught question: the same question stored twice is merged
    into the answer written last. Returns how many were removed."""
    kept: dict[str, KnowledgeEntry] = {}
    removed = 0
    entries = (
        db.query(KnowledgeEntry).filter(KnowledgeEntry.origin != "system")
        .order_by(KnowledgeEntry.updated_at.desc(), KnowledgeEntry.created_at.desc()).all()
    )
    for entry in entries:
        key = question_key(entry.title)
        latest = kept.get(key)
        if latest is None:
            kept[key] = entry
            continue
        latest.use_count = (latest.use_count or 0) + (entry.use_count or 0)
        for item in wordings_of(entry)[1:]:
            add_wording(latest, item["q"], sure=item["sure"])
        db.delete(entry)
        removed += 1
    if removed:
        db.commit()
    return removed
