"""Which program a file belongs to (a course file added to a class, a file added to a program), by its words.

Each program is described by what the application knows of it: its name, description and week-by-week plan, what
9antra.tn says of each week, its questions of the level check, its accepted files, and the words of its subject
(“docker, kubernetes, pipeline…” for DevOps). A file's words are weighed by how much they belong to one program rather
than to all (a word every program has counts little), and each program gets the share of the file it covers.

  fits     the file is about its own program (or nearly as much as about any other): it is used at once;
  other    it is clearly more about another program: it waits for the admin, who can accept it, move it or remove it;
  off      it looks like none of the programs: it waits for the admin too;
  unknown  there is too little to read (a video with no note, a photo): it is used, marked “not checked”.

No model decides: the same words always give the same answer, and the words that decided are shown to the admin.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from sqlalchemy.orm import Session

MIN_WORDS = 3            # fewer distinct words than this: nothing to tell
OFF = 0.18               # the best program covers less than this of the file: none of them
CLOSE = 0.72             # its own program covers at least this share of what the best one covers: it fits
WELL_COVERED = 0.4       # or its own program covers this much of the file by itself

# The words of each subject (French and English), as they appear in its courses.
SUBJECT_WORDS = {
    "python": "python pip django flask pandas numpy matplotlib jupyter notebook variable variables fonction fonctions function boucle boucles loop "
              "liste listes list tuple dictionnaire dictionary set classe classes objet objets heritage module modules package exception exceptions "
              "pytest virtualenv venv script scripts input print def lambda comprehension fichier fichiers json csv api requests",
    "devops": "docker dockerfile container conteneur conteneurs image images compose kubernetes k8s pod pods deployment deploiement cluster helm "
              "jenkins gitlab github actions pipeline pipelines ci cd integration continue ansible terraform vagrant prometheus grafana monitoring "
              "linux bash shell nginx apache aws azure gcp cloud registry volume volumes orchestration microservices yaml devops sre",
    "flutter": "flutter dart widget widgets stateful stateless scaffold pubspec android ios mobile application emulateur emulator firebase provider "
               "bloc navigator route routes listview gridview material cupertino http dio json api rest backend node express spring boot jwt",
    "mern": "react reactjs node nodejs express expressjs mongodb mongoose jsx hook hooks usestate useeffect redux npm javascript js es6 promise "
            "async await api rest jwt axios router component components props state frontend backend mern vite next",
    "spring": "spring boot springboot java jpa hibernate maven gradle angular typescript component components service services injection rest "
              "controller entity entities repository repositories mysql postgresql jwt security microservices api backend frontend fullstack",
    "bi": "power bi powerbi dax power query tableau etl entrepot donnees data warehouse datawarehouse kpi dashboard dashboards tableaux bord olap cube "
          "sql requete requetes visualisation visualization talend ssis ssas reporting rapport rapports dimension fait faits modelisation excel",
    "iot": "arduino esp32 esp8266 raspberry pi capteur capteurs sensor sensors mqtt actionneur actionneurs gpio iot node red lora lorawan zigbee wifi "
           "bluetooth ble led relais relay microcontroleur microcontroller broker thingspeak firmware embarque embedded protocole protocol",
    "cyber": "securite security cybersecurite cybersecurity firewall pare feu vpn chiffrement encryption cryptographie malware ransomware phishing "
             "pentest owasp xss injection nmap wireshark kali metasploit vulnerabilite vulnerabilites vulnerability siem soc iso 27001 attaque "
             "attaques hacking ethique authentification password mot passe reseau reseaux",
    "marketing": "seo sea google ads meta facebook instagram tiktok linkedin community manager contenu contenus content persona funnel tunnel "
                 "campagne campagnes campaign emailing newsletter canva branding marque audience ciblage budget kpi wordpress landing page "
                 "publicite publicites reseaux sociaux social media analytics conversion copywriting video capcut",
    "ai": "intelligence artificielle artificial machine learning deep apprentissage automatique reseau neurones neural network tensorflow pytorch "
          "keras nlp chatbot chatbots llm gpt transformer transformers embedding embeddings rag langchain dataset donnees entrainement training "
          "modele modeles model prompt classification regression scikit sklearn",
    "web": "html css javascript dom responsive bootstrap navigateur browser balise balises flexbox grid formulaire site web page pages",
}


def _counts(text: str, surface: dict | None = None) -> Counter:
    """The words of a text that carry meaning, counted (accents, capitals, plurals and stop words aside). `surface`
    gets, for each, the word as the text writes it (to show it)."""
    from app.services.knowledge_service import tokenize

    found: Counter = Counter()
    for word in re.findall(r"\S+", text or ""):
        for token in tokenize(word):
            if len(token) > 1 and not token.isdigit():
                found[token] += 1
                if surface is not None and not token.startswith("_"):
                    surface.setdefault(token, re.sub(r"^[^\w]+|[^\w]+$", "", word).lower())
    return found


def _filename_words(filename: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", filename or "")
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)
    return re.sub(r"[_\-.()\[\]+]+", " ", stem)


def program_text(db: Session, program) -> str:
    """Everything the application knows of a program, in one text (its accepted files included, in part)."""
    from app.models.course_material import ClassMaterial, ProgramFile
    from app.models import ClassGroup
    from app.services import chatbot_sources, level_bank

    weeks, _ = chatbot_sources._weeks_of(db, program)
    parts = [(program.name or "") + " " + (program.name or ""), program.description or ""]
    parts += [f"{week['title'] or ''} {week['objectives'] or ''} {week['site'] or ''}" for week in weeks]
    for level in program.levels:
        parts.append(f"{level.title or ''} {level.description or ''}")
    parts += [item["question"] + " " + " ".join(item["options"][:1]) for item in level_bank.questions_for(program.name, program.website_slug)]
    subject = level_bank.subject_of(program.name, program.website_slug)
    if subject in SUBJECT_WORDS:
        parts.append(SUBJECT_WORDS[subject] + " " + SUBJECT_WORDS[subject])
    taken = 0
    for row in db.query(ProgramFile).filter(ProgramFile.program_id == program.id, ProgramFile.review_status != "waiting",
                                            ProgramFile.learned_text.isnot(None)).limit(20).all():
        piece = (row.learned_text or "")[:8000]
        parts.append(piece)
        taken += len(piece)
    ids = [group.id for group in db.query(ClassGroup.id).filter(ClassGroup.program_id == program.id).all()]
    if ids and taken < 30000:
        for row in (db.query(ClassMaterial).filter(ClassMaterial.class_group_id.in_(ids), ClassMaterial.learned_text.isnot(None),
                                                   (ClassMaterial.review_status.is_(None)) | (ClassMaterial.review_status == "ok"))
                    .limit(20).all()):
            parts.append((row.learned_text or "")[:6000])
    return "\n".join(part for part in parts if part)


def judge(db: Session, *, own_program_id: str | None, text: str = "", filename: str = "", note: str = "",
          skip_file: str | None = None) -> dict:
    """{"fit": fits | other | off | unknown, "program_id": the program it is about (its own when it fits),
    "own": share covered by its own program, "best": by the best one, "words": the words that decided}."""
    from app.models import Program

    name_words = _filename_words(filename)
    surface: dict[str, str] = {}
    words = _counts(" ".join([name_words, name_words, name_words, note or "", (text or "")[:80000]]), surface)
    if len(words) < MIN_WORDS:
        return {"fit": "unknown", "program_id": own_program_id, "own": None, "best": None, "words": []}
    programs = db.query(Program).all()
    if not programs:
        return {"fit": "unknown", "program_id": own_program_id, "own": None, "best": None, "words": []}
    profiles = {program.id: set(_counts(program_text(db, program))) for program in programs}
    frequency = Counter(token for profile in profiles.values() for token in profile)
    total_programs = len(profiles)

    def weight(token: str, count: int) -> float:
        # A word no program has weighs like the rarest one: it pulls the file away from every program alike.
        return min(count, 4) * math.log(1 + total_programs / (1 + frequency.get(token, 0)))

    weights = {token: weight(token, count) for token, count in words.items()}
    whole = sum(weights.values()) or 1.0
    shares = {program_id: sum(value for token, value in weights.items() if token in profile) / whole
              for program_id, profile in profiles.items()}
    best_id = max(shares, key=shares.get)
    best, own = shares[best_id], shares.get(own_program_id, 0.0) if own_program_id else 0.0

    def deciding(program_id: str) -> list[str]:
        profile = profiles.get(program_id, set())
        # The words that say the most about this program rather than all of them, as the file writes them.
        kept = sorted((token for token in weights if token in profile and token in surface and frequency.get(token, 0) <= total_programs / 2),
                      key=lambda token: -weights[token])
        return list(dict.fromkeys(surface[token] for token in kept))[:6]

    if best < OFF:
        return {"fit": "off", "program_id": None, "own": round(own, 3), "best": round(best, 3), "words": deciding(best_id)}
    # Its own program covers it nearly as well as the best one, or covers much of it (a bootcamp teaching a bit of all).
    if own_program_id is not None and (best_id == own_program_id or own >= CLOSE * best or (own >= WELL_COVERED and own >= 0.55 * best)):
        return {"fit": "fits", "program_id": own_program_id, "own": round(own, 3), "best": round(best, 3), "words": deciding(own_program_id)}
    return {"fit": "other", "program_id": best_id, "own": round(own, 3), "best": round(best, 3), "words": deciding(best_id)}


def said(verdict: dict | None, own_name: str = "", other_name: str = "") -> str:
    """The verdict in one sentence for the page, in its language."""
    from app.services.ui_words import t as _t

    fit = (verdict or {}).get("fit")
    words = ", ".join((verdict or {}).get("words") or [])
    if fit == "fits":
        return _t("Part of {0}: {1}.", own_name, words) if words else _t("Part of {0}.", own_name)
    if fit == "other":
        return _t("Looks like part of {0}, not {1}: {2}.", other_name, own_name, words) if words else _t("Looks like part of {0}, not {1}.", other_name, own_name)
    if fit == "off":
        return _t("Does not look like part of {0}, nor of any other program.", own_name)
    return _t("Not checked: there is nothing to read in it. Write a few lines about what it shows.")
