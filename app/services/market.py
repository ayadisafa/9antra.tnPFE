"""What employers ask for, program by program.

Job offers come from three places: the offers published on 9antra.tn (already
kept by website_sync), a public job board read when the admin asks, and offers
the admin pastes (from LinkedIn, which no program may read by itself).

Nothing is guessed and no language model is asked. For each program the offers
of its field are found by their title, the skills each one names are counted
from a fixed list, and the percentages are those counts:

    asked     the share of the field's offers that name a skill
    covered   whether the program's own text names it too
    fit       of everything asked, the share the program already covers
              (each skill weighing as much as it is asked for)
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import requests
from sqlalchemy.orm import Session

from app.models.market_offer import MarketOffer
from app.models.program import Program
from app.models.website import WebsiteItem
from app.services import level_bank, market_content

logger = logging.getLogger(__name__)

BOARD = "https://remotive.com/api/remote-jobs"      # public, free, asks to be read a few times a day at most
BOARD_2 = "https://www.arbeitnow.com/api/job-board-api"    # public, free; three pages of 300 offers
BOARD_3 = "https://jobicy.com/api/v2/remote-jobs"         # public, free; fifty offers
BOARD_4 = "https://himalayas.app/jobs/api"                # public, free; twenty offers a page
BOARDS = ("remotive", "arbeitnow", "jobicy", "himalayas")
OFFERS_SHOWN = 40
PUBLIC = "Public job boards"
KEEP_OFFERS = 700
READ_AGAIN_AFTER = timedelta(hours=6)
READ_EVERY = timedelta(hours=24)      # by itself, once a day
CONTENT_IDEAS = 8
ENOUGH = 5              # offers of a field below which the percentages are only a hint
KEEP_TEXT = 6000
WORTH_ADDING = 15       # percent of offers from which a missing skill is worth adding

# subject -> the skills of the field: (name shown, how offers write it)
SKILLS: dict[str, list[tuple[str, str]]] = {
    "devops": [
        ("Docker", r"docker"), ("Kubernetes", r"kubernetes|k8s"), ("CI/CD", r"ci ?/ ?cd|continuous (integration|delivery|deployment)|int[ée]gration continue"),
        ("Jenkins", r"jenkins"), ("GitLab CI", r"gitlab"), ("GitHub Actions", r"github actions"), ("Terraform", r"terraform"),
        ("Ansible", r"ansible"), ("AWS", r"aws|amazon web services"), ("Azure", r"azure"), ("Google Cloud", r"gcp|google cloud"),
        ("Linux", r"linux"), ("Bash", r"bash|shell script\w*"), ("Python", r"python"), ("Monitoring", r"prometheus|grafana|monitoring|observability|datadog"),
        ("Helm", r"helm"), ("GitOps / ArgoCD", r"argo ?cd|gitops"), ("SonarQube", r"sonarqube"), ("Microservices", r"micro-?services"),
        ("DevSecOps", r"devsecops|security scanning"),
    ],
    "python": [
        ("Python", r"python"), ("Django", r"django"), ("Flask", r"flask"), ("FastAPI", r"fastapi"), ("SQL", r"sql|postgres(ql)?|mysql"),
        ("Pandas", r"pandas"), ("NumPy", r"numpy"), ("REST API", r"rest(ful)? ?apis?|apis? rest"), ("Docker", r"docker"),
        ("Testing", r"pytest|unit tests?|tests? unitaires|tdd"), ("Object-oriented programming", r"object[- ]oriented|oop|orient[ée]e? objet"),
        ("Linux", r"linux"), ("AWS", r"aws"), ("Data analysis", r"data analysis|analyse de donn[ée]es"), ("Automation", r"automation|scripting|automatisation"),
        ("Async programming", r"asyncio|asynchronous|celery"), ("Machine learning", r"machine learning|scikit-?learn"),
    ],
    "flutter": [
        ("Flutter", r"flutter"), ("Dart", r"dart"), ("Firebase", r"firebase"), ("REST API", r"rest(ful)? ?apis?|apis? rest"),
        ("State management", r"bloc|provider|riverpod|getx|state management"), ("Android", r"android"), ("iOS", r"ios"),
        ("Kotlin", r"kotlin"), ("Swift", r"swift"), ("UI/UX and Figma", r"ui ?/ ?ux|figma"), ("Node.js", r"node\.?js"),
        ("Spring Boot", r"spring ?boot"), ("CI/CD", r"ci ?/ ?cd|fastlane|codemagic"), ("Push notifications", r"push notifications?|fcm"),
        ("Local storage", r"sqlite|hive|shared preferences"), ("Testing", r"unit tests?|widget tests?|tests? unitaires"),
        ("Publishing to the stores", r"play store|app store|google play"), ("React Native", r"react native"),
    ],
    "mern": [
        ("JavaScript", r"javascript"), ("TypeScript", r"typescript"), ("React", r"react(\.js)?"), ("Node.js", r"node\.?js"),
        ("Express", r"express(\.js)?"), ("MongoDB", r"mongodb|mongo"), ("Next.js", r"next\.?js"), ("Redux", r"redux|zustand"),
        ("REST API", r"rest(ful)? ?apis?|apis? rest"), ("GraphQL", r"graphql"), ("HTML/CSS", r"html5?|css3?"), ("Tailwind", r"tailwind\w*"),
        ("Docker", r"docker"), ("PostgreSQL", r"postgres(ql)?"), ("Testing", r"jest|cypress|playwright|unit tests?"),
        ("WebSockets", r"websockets?|socket\.io"), ("Authentication (JWT, OAuth)", r"jwt|oauth|authentication"), ("AWS", r"aws"),
        ("CI/CD", r"ci ?/ ?cd"),
    ],
    "spring": [
        ("Java", r"java"), ("Spring Boot", r"spring ?boot|spring"), ("Angular", r"angular"), ("TypeScript", r"typescript"),
        ("Hibernate / JPA", r"hibernate|jpa"), ("SQL", r"sql|mysql|postgres(ql)?|oracle"), ("REST API", r"rest(ful)? ?apis?|apis? rest"),
        ("Microservices", r"micro-?services"), ("Docker", r"docker"), ("Kubernetes", r"kubernetes|k8s"), ("Maven / Gradle", r"maven|gradle"),
        ("JUnit", r"junit|unit tests?|tests? unitaires"), ("Kafka / RabbitMQ", r"kafka|rabbitmq"), ("Spring Security", r"spring security|jwt|oauth"),
        ("CI/CD", r"ci ?/ ?cd|jenkins"), ("HTML/CSS", r"html5?|css3?"), ("Cloud (AWS, Azure)", r"aws|azure"),
    ],
    "bi": [
        ("SQL", r"sql"), ("Power BI", r"power ?bi"), ("Tableau", r"tableau"), ("Excel", r"excel"), ("Python", r"python"),
        ("ETL", r"etl|elt|talend|ssis|data pipelines?"), ("Data warehouse", r"data ?warehouse|entrep[oô]t de donn[ée]es|snowflake|bigquery|redshift"),
        ("DAX", r"dax"), ("Data modelling", r"data model+ing|mod[ée]lisation|kimball|star schema"), ("Statistics", r"statisti\w+"),
        ("Machine learning", r"machine learning"), ("dbt", r"dbt"), ("Airflow", r"airflow"), ("Looker / Qlik", r"looker|qlik\w*"),
        ("Dashboards and reporting", r"dashboards?|tableaux? de bord|reporting"), ("Azure data tools", r"azure|synapse|databricks|fabric"),
        ("Spark", r"spark"),
    ],
    "iot": [
        ("ESP32 / Arduino", r"esp32|esp8266|arduino"), ("Raspberry Pi", r"raspberry ?pi"), ("C / C++", r"c\+\+|embedded c|langage c|c/c\+\+"),
        ("MQTT", r"mqtt"), ("Python", r"python"), ("Sensors", r"sensors?|capteurs?"), ("Embedded Linux", r"embedded linux|linux embarqu[ée]|yocto"),
        ("RTOS", r"rtos|freertos|zephyr"), ("Bluetooth / BLE", r"bluetooth|ble"), ("LoRa", r"lora(wan)?"),
        ("Electronics and PCB", r"pcb|electronics?|[ée]lectronique"), ("Firmware", r"firmware"),
        ("IoT cloud platforms", r"aws iot|azure iot|thingsboard|iot (cloud|platform)"), ("Node-RED", r"node-?red"), ("Wi-Fi", r"wi-?fi"),
        ("Industrial protocols", r"modbus|plc|scada|can bus|opc[- ]ua"), ("Edge AI", r"edge ai|tinyml"), ("Security", r"security|s[ée]curit[ée]"),
    ],
    "cyber": [
        ("Penetration testing", r"pen(etration)?[- ]?test\w*|tests? d'intrusion"), ("SIEM", r"siem|splunk|qradar|sentinel|elk"),
        ("SOC", r"soc"), ("Network security", r"network security|firewalls?|s[ée]curit[ée] r[ée]seau"), ("Linux", r"linux"),
        ("Kali Linux", r"kali"), ("OWASP", r"owasp"), ("Python", r"python"), ("Incident response", r"incident response|r[ée]ponse aux incidents"),
        ("Standards (ISO 27001, NIST, GDPR)", r"iso ?27001|nist|gdpr|rgpd|compliance|conformit[ée]"), ("Cloud security", r"cloud security|aws|azure"),
        ("Active Directory", r"active directory"), ("Vulnerability management", r"vulnerabilit\w+|nessus|qualys"), ("Burp Suite", r"burp"),
        ("Scripting", r"bash|powershell"), ("Threat intelligence", r"threat (intel\w*|hunting)|mitre"),
        ("Certifications (CEH, OSCP, CISSP)", r"ceh|oscp|cissp|security\+"), ("Cryptography", r"cryptograph\w+|chiffrement|pki"),
        ("Red teaming", r"red team\w*"), ("Forensics", r"forensi\w+"),
    ],
    "marketing": [
        ("SEO", r"seo"), ("Google Ads", r"google ads|sem|ppc|adwords"), ("Meta Ads", r"meta ads|facebook ads|instagram ads|paid social|social ads"),
        ("Content", r"content (marketing|creation|strategy|writing)|r[ée]daction|copywriting"),
        ("Social media", r"social media|r[ée]seaux sociaux|community manag\w+"), ("Google Analytics", r"google analytics|ga4"),
        ("Email marketing", r"email(ing)? marketing|e-?mailing|newsletters?|mailchimp|klaviyo"), ("WordPress", r"wordpress"),
        ("Design (Canva, Adobe)", r"canva|photoshop|adobe|graphic design|design graphique"),
        ("Video", r"video (editing|production)|montage vid[ée]o|capcut|premiere|reels"), ("CRM", r"crm|hubspot|salesforce"),
        ("AI tools", r"chatgpt|generative ai|ia g[ée]n[ée]rative|prompt\w*"), ("E-commerce", r"e-?commerce|shopify|woocommerce"),
        ("Branding", r"brand(ing)?"), ("Marketing automation", r"marketing automation"), ("KPIs and reporting", r"kpis?|reporting|roi"),
        ("TikTok", r"tiktok"), ("Influence", r"influence\w*"), ("Conversion (landing pages)", r"cro|conversion rate|landing pages?"),
    ],
    "ai": [
        ("Python", r"python"), ("Machine learning", r"machine learning"), ("Deep learning", r"deep learning|neural networks?"),
        ("NLP", r"nlp|natural language|traitement du langage"), ("LLMs / generative AI", r"llms?|large language models?|gpt|generative ai|genai|ia g[ée]n[ée]rative"),
        ("LangChain", r"langchain|llamaindex"), ("RAG and vector search", r"rag|retrieval[- ]augmented|vector (db|database|store|search)s?|embeddings?"),
        ("PyTorch", r"pytorch"), ("TensorFlow", r"tensorflow|keras"), ("Hugging Face", r"hugging ?face|transformers"), ("Pandas", r"pandas"),
        ("SQL", r"sql"), ("MLOps", r"mlops|model deployment"), ("Docker", r"docker"), ("Cloud (AWS, Azure, GCP)", r"aws|azure|gcp"),
        ("FastAPI / Flask", r"fastapi|flask"), ("Computer vision", r"computer vision|opencv"), ("Prompt engineering", r"prompt engineering|prompting"),
        ("Chatbots", r"chatbots?|conversational"), ("Scikit-learn", r"scikit-?learn|sklearn"), ("Recommender systems", r"recommend\w+ systems?|syst[èe]mes? de recommandation"),
    ],
    "web": [
        ("HTML", r"html5?"), ("CSS", r"css3?|sass|scss"), ("JavaScript", r"javascript"), ("React", r"react(\.js)?"), ("TypeScript", r"typescript"),
        ("Responsive design", r"responsive"), ("Node.js", r"node\.?js"), ("Tailwind / Bootstrap", r"tailwind\w*|bootstrap"),
        ("REST API", r"rest(ful)? ?apis?|apis? rest"), ("Vue", r"vue(\.js)?|nuxt(\.js)?"), ("Angular", r"angular"), ("Next.js", r"next\.?js"),
        ("SQL", r"sql|mysql|postgres(ql)?"), ("WordPress", r"wordpress"), ("Figma and UI/UX", r"figma|ui ?/ ?ux"), ("Testing", r"jest|cypress|unit tests?"),
        ("Accessibility", r"accessibilit\w+|a11y|wcag"), ("SEO", r"seo"), ("PHP / Laravel", r"php|laravel"), ("Docker", r"docker"),
        ("Web performance", r"web performance|core web vitals"),
    ],
}

# What offers ask of everybody, whatever the field.
COMMON = [
    ("English", r"english|anglais"), ("French", r"french|fran[çc]ais"), ("Communication", r"communication"),
    ("Teamwork", r"team ?work|travail d'[ée]quipe|collaborat\w+"), ("Agile / Scrum", r"agile|scrum|kanban|jira"),
    ("Git", r"git(hub|lab)?"), ("Problem solving", r"problem[- ]solving|r[ée]solution de probl[èe]mes"),
    ("Autonomy", r"autonom\w+|self[- ]starter|independently"),
]

# subject -> what the title of an offer of that field says
TITLES = {
    "devops": r"devops|sre|site reliability|platform engineer|cloud engineer|infrastructure engineer|kubernetes",
    "python": r"python",
    "flutter": r"flutter|mobile (app\w* )?(developer|engineer)|d[ée]veloppeur mobile|android|ios (developer|engineer)",
    "mern": r"react|node\.?js|full[- ]?stack|mern|javascript|next\.?js|typescript",
    "spring": r"java(?!script)|spring|angular",
    "bi": r"business intelligence|bi (developer|analyst|engineer)|data analyst|power ?bi|analytics engineer|data engineer|tableau",
    "iot": r"iot|embedded|firmware|internet of things",
    "cyber": r"security|cyber\w*|pen(etration)? ?test\w*|soc analyst|infosec",
    "marketing": r"marketing|seo|social media|content (manager|strategist|writer|creator)|community manager|brand manager",
    "ai": (r"machine learning|ml engineer|data scientist|artificial intelligence|intelligence artificielle|ai ?/ ?ml|ml ?/ ?ai|"
           r"(ai|ki|genai|llm|nlp)[ -]?(engineer|developer|entwickler|specialist|researcher|scientist|architect|platform engineer|product engineer)"),
    "web": r"front[- ]?end|web (developer|designer)|wordpress|html|ui developer|d[ée]veloppeur web|int[ée]grateur",
}
# Fields of the trade that no program here is sorted under: the name shown, what a title says,
# the skills counted, and the fields taught here that come closest.
OTHER: dict[str, tuple[str, str, list[tuple[str, str]], tuple[str, ...]]] = {
    "datascience": ("Data science", r"data scien\w+|applied scientist|research scientist|statistician|quantitative analyst", [
        ("Python", r"python"), ("SQL", r"sql"), ("Machine learning", r"machine learning"), ("Statistics", r"statisti\w+"),
        ("Pandas / NumPy", r"pandas|numpy"), ("Deep learning", r"deep learning|neural networks?"), ("Data visualisation", r"data visuali[sz]ation|tableau|power ?bi|matplotlib"),
        ("Spark", r"spark"), ("R", r"rstudio|r programming|python[ ,/]+(and |or )?r|r[ ,/]+(and |or )?python"), ("Experiments (A/B tests)", r"a/b test\w*|experimentation"),
        ("Cloud", r"aws|azure|gcp"), ("MLOps", r"mlops|model deployment"), ("NLP", r"nlp|natural language"), ("Forecasting", r"forecast\w+|time series"),
        ("LLMs / generative AI", r"llms?|generative ai|genai"),
    ], ("ai", "bi", "python")),
    "dataeng": ("Data engineering", r"data engineer\w*|big data|analytics engineer|data platform|dataops|etl developer", [
        ("SQL", r"sql"), ("Python", r"python"), ("Spark", r"spark"), ("Airflow", r"airflow"), ("Kafka", r"kafka"), ("dbt", r"dbt"),
        ("Snowflake", r"snowflake"), ("BigQuery", r"bigquery"), ("Databricks", r"databricks"), ("ETL pipelines", r"etl|elt|data pipelines?"),
        ("AWS", r"aws"), ("Azure", r"azure"), ("Data warehouse", r"data ?warehouse|lakehouse|data lake"), ("Data modelling", r"data model+ing"),
        ("Docker", r"docker"), ("Scala / Java", r"scala|java"),
    ], ("bi", "python")),
    "cloud": ("Cloud", r"cloud (architect|consultant|developer|specialist|administrator|solutions?)|aws|azure|gcp|solutions? architect", [
        ("AWS", r"aws|amazon web services"), ("Azure", r"azure"), ("Google Cloud", r"gcp|google cloud"), ("Kubernetes", r"kubernetes|k8s"),
        ("Terraform", r"terraform"), ("Serverless", r"serverless|lambda"), ("Networking", r"networking|vpc|dns"), ("Identity and access", r"iam|identity"),
        ("Linux", r"linux"), ("Docker", r"docker"), ("CI/CD", r"ci ?/ ?cd"), ("Cost control (FinOps)", r"finops|cost optimi[sz]ation"),
        ("Cloud certifications", r"aws certified|az-\d+|certified solutions architect"),
    ], ("devops",)),
    "qa": ("Software testing (QA)", r"qa (engineer|automation|tester|analyst|lead)|test automation|software tester|sdet|test engineer|testautomatisier\w+|softwaretester", [
        ("Test automation", r"test automation|automated tests?|testautomatisier\w+"), ("Selenium", r"selenium"), ("Cypress", r"cypress"),
        ("Playwright", r"playwright"), ("API testing (Postman)", r"postman|api test\w*"), ("ISTQB", r"istqb"), ("CI/CD", r"ci ?/ ?cd|jenkins"),
        ("Java", r"java"), ("Python", r"python"), ("JavaScript", r"javascript|typescript"), ("Performance tests (JMeter)", r"jmeter|performance test\w*|load test\w*"),
        ("Manual testing", r"manual test\w*|test cases?"), ("Jira", r"jira"),
    ], ("mern", "spring")),
    "uiux": ("UI/UX design", r"ux|ui ?/ ?ux|product designer|ui designer|interaction designer", [
        ("Figma", r"figma"), ("User research", r"user research|user interviews?"), ("Prototyping", r"prototyp\w+"), ("Design systems", r"design systems?"),
        ("Wireframes", r"wireframes?"), ("Usability testing", r"usability"), ("Adobe XD / Sketch", r"adobe xd|sketch"), ("HTML/CSS", r"html5?|css3?"),
        ("Accessibility", r"accessibilit\w+|wcag"), ("Motion design", r"motion|after effects"),
    ], ("web", "marketing")),
    "dotnet": (".NET / C#", r"\.net|c#|dotnet|asp\.net", [
        ("C#", r"c#"), (".NET / ASP.NET", r"\.net|asp\.net|dotnet"), ("Azure", r"azure"), ("SQL Server", r"sql server|mssql|t-sql"),
        ("Entity Framework", r"entity framework"), ("Angular", r"angular"), ("React", r"react"), ("Blazor", r"blazor"),
        ("Microservices", r"micro-?services"), ("REST API", r"rest(ful)? ?apis?"), ("Docker", r"docker"),
    ], ("spring",)),
    "php": ("PHP (Laravel, Symfony)", r"php|laravel|symfony|magento|shopware", [
        ("PHP", r"php"), ("Laravel", r"laravel"), ("Symfony", r"symfony"), ("MySQL", r"mysql|mariadb"), ("JavaScript", r"javascript"),
        ("Vue", r"vue(\.js)?"), ("WordPress", r"wordpress"), ("REST API", r"rest(ful)? ?apis?"), ("Docker", r"docker"), ("Shop systems", r"shopware|magento|woocommerce"),
    ], ("web",)),
    "erp": ("Business software (SAP, Odoo, Salesforce)", r"sap|odoo|salesforce|dynamics 365|servicenow", [
        ("SAP", r"sap"), ("ABAP", r"abap"), ("S/4HANA", r"s/4 ?hana|hana"), ("Odoo", r"odoo"), ("Salesforce", r"salesforce"),
        ("Dynamics 365", r"dynamics 365"), ("ServiceNow", r"servicenow"), ("SQL", r"sql"), ("Python", r"python"),
    ], ("bi", "python")),
}
# The fields taught here, as the admin's pages name them.
FIELD_NAMES = {
    "devops": "DevOps", "python": "Python", "flutter": "Mobile (Flutter)", "mern": "Web JavaScript (React, Node)",
    "spring": "Web Java (Spring Boot, Angular)", "bi": "Business intelligence", "iot": "Internet of things (IoT)",
    "cyber": "Cybersecurity", "marketing": "Digital marketing", "ai": "Artificial intelligence", "web": "Web basics (HTML, CSS)",
}
# What to type in a job search for each field (the admin's own search, in their own browser).
SEARCH = {
    "devops": "DevOps", "python": "Python developer", "flutter": "Flutter developer", "mern": "React Node.js developer",
    "spring": "Java Spring Boot Angular developer", "bi": "Business Intelligence Power BI", "iot": "IoT embedded",
    "cyber": "Cybersecurity", "marketing": "Digital marketing", "ai": "AI machine learning engineer", "web": "Front-end web developer",
}


def _pattern(words: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])(?:" + words + r")(?![A-Za-z0-9])", re.IGNORECASE)


_SKILL = {subject: [(name, _pattern(words)) for name, words in skills] for subject, skills in SKILLS.items()}
_COMMON = [(name, _pattern(words)) for name, words in COMMON]
_TITLE = {subject: _pattern(words) for subject, words in TITLES.items()}
_OTHER_TITLE = {key: _pattern(words) for key, (_, words, _, _) in OTHER.items()}
_OTHER_SKILL = {key: [(name, _pattern(words)) for name, words in skills] for key, (_, _, skills, _) in OTHER.items()}
_TAGS = re.compile(r"<[^>]+>")


def plain(text: str | None) -> str:
    """An offer's text without its layout."""
    return " ".join(html.unescape(_TAGS.sub(" ", text or "")).split())[:KEEP_TEXT]


def skills_in(text: str, subject: str) -> set[str]:
    """The skills of a field that a text names."""
    return {name for name, pattern in _SKILL.get(subject, []) if pattern.search(text or "")}


def common_in(text: str) -> set[str]:
    return {name for name, pattern in _COMMON if pattern.search(text or "")}


# The skills without which an offer is not of the field, whatever else it names.
ANCHORS = {
    "devops": {"Kubernetes", "CI/CD", "Terraform", "Ansible", "Jenkins", "Helm", "GitOps / ArgoCD"}, "python": {"Python"}, "flutter": {"Flutter"},
    "mern": {"React", "Node.js"}, "spring": {"Java", "Spring Boot", "Angular"}, "bi": {"Power BI", "Tableau", "ETL", "Data warehouse"},
    "iot": {"ESP32 / Arduino", "MQTT", "Firmware", "Embedded Linux", "RTOS"}, "cyber": {"Penetration testing", "SIEM", "SOC", "OWASP"},
    "marketing": {"SEO", "Google Ads", "Meta Ads", "Social media"}, "ai": {"Machine learning", "LLMs / generative AI", "NLP", "Deep learning"},
    "web": {"HTML", "CSS"},
}
# A word in a title that is not enough alone ("AI" is in many product names): the offer must also name two skills of the field.
_HINTS = {"ai": _pattern(r"ai|ia")}


def fields_of(title: str, text: str = "") -> list[str]:
    """The fields an offer belongs to: by its title, else by the skills it names
    (three of one field at least, one of which defines the field)."""
    by_title = [subject for subject, pattern in _TITLE.items() if pattern.search(title or "")]
    if by_title:
        return by_title
    counted = []
    for subject in SKILLS:
        named = skills_in(text, subject)
        if len(named) >= 3 and named & ANCHORS[subject]:
            counted.append((len(named), subject))
    found = [subject for _, subject in sorted(counted, reverse=True)[:2]]
    for subject, hint in _HINTS.items():
        if subject not in found and hint.search(title or "") and len(skills_in(text, subject)) >= 2:
            found.append(subject)
    return found


def others_of(title: str) -> list[str]:
    """The fields no program here teaches that an offer's title names."""
    return [key for key, pattern in _OTHER_TITLE.items() if pattern.search(title or "")]


# ------------------------------------------------------------------
# The offers
# ------------------------------------------------------------------

def _offer(source: str, title: str, text: str, *, company: str = "", location: str = "", url: str = "",
           posted=None, key: str = "") -> dict:
    whole = f"{title}. {text}"
    if posted is not None and posted.tzinfo is not None:
        posted = posted.replace(tzinfo=None)
    return {"id": key, "source": source, "title": title, "company": company, "location": location, "url": url,
            "posted": posted, "text": whole, "fields": fields_of(title, whole), "others": others_of(title)}


def website_offers(db: Session) -> list[dict]:
    """The internship and job offers published on the website."""
    found = []
    for item in db.query(WebsiteItem).filter(WebsiteItem.kind == "offer", WebsiteItem.gone_at.is_(None)).all():
        details = item.details
        skills = ", ".join(str(skill) for skill in details.get("skills") or [] if isinstance(skill, str))
        text = plain(f"{details.get('description') or item.summary or ''} {skills}")
        found.append(_offer("9antra.tn", item.title or "", text, company=str(details.get("company") or ""),
                            location=str(details.get("location") or ""), url=item.url or "", posted=item.first_seen_at, key=item.id))
    return found


def kept_offers(db: Session) -> list[dict]:
    """The offers read from the job board or pasted by the admin."""
    return [
        _offer("Pasted by you" if row.source == "pasted" else PUBLIC, row.title, row.text, company=row.company or "",
               location=row.location or "", url=row.url or "", posted=row.posted_at or row.added_at, key=row.id)
        for row in db.query(MarketOffer).order_by(MarketOffer.added_at.desc()).all()
    ]


def all_offers(db: Session) -> list[dict]:
    """The offers the analysis counts: other platforms and what the admin pasted. The centre's own internship
    offers on 9antra.tn are not the market, so they are left out."""
    return kept_offers(db)


def fetch_public() -> list[dict]:
    """The offers of the public job boards, in one shape: {"source", "id", "title", "description", "tags",
    "company_name", "candidate_required_location", "url", "publication_date"}. Four requests in all.
    A board that does not answer is left out; when none does, this raises. (Replaced in tests.)"""
    import time

    headers = {"User-Agent": "training-centre-catalogue/1.0"}
    found, failed = [], 0
    try:
        answer = requests.get(BOARD, timeout=40, headers=headers)
        answer.raise_for_status()
        found += [dict(job, source="remotive") for job in answer.json().get("jobs") or [] if isinstance(job, dict)]
    except Exception as exc:
        failed += 1
        logger.warning("Remotive could not be read: %s", exc)
    try:
        for page in (1, 2, 3):
            answer = requests.get(BOARD_2, params={"page": page}, timeout=40, headers=headers)
            answer.raise_for_status()
            for job in answer.json().get("data") or []:
                if not isinstance(job, dict) or not job.get("slug"):
                    continue
                created = job.get("created_at")
                found.append({
                    "source": "arbeitnow", "id": job["slug"], "title": job.get("title"), "description": job.get("description"),
                    "tags": job.get("tags") or [], "company_name": job.get("company_name"),
                    "candidate_required_location": job.get("location"), "url": job.get("url"),
                    "publication_date": datetime.utcfromtimestamp(created).isoformat() if isinstance(created, (int, float)) else "",
                })
            time.sleep(1)
    except Exception as exc:
        failed += 1
        logger.warning("Arbeitnow could not be read: %s", exc)
    try:
        answer = requests.get(BOARD_3, params={"count": 50}, timeout=40, headers=headers)
        answer.raise_for_status()
        for job in answer.json().get("jobs") or []:
            if isinstance(job, dict) and job.get("id"):
                found.append({"source": "jobicy", "id": job["id"], "title": job.get("jobTitle"), "description": job.get("jobDescription") or job.get("jobExcerpt"),
                              "tags": job.get("jobIndustry") if isinstance(job.get("jobIndustry"), list) else [], "company_name": job.get("companyName"),
                              "candidate_required_location": job.get("jobGeo"), "url": job.get("url"), "publication_date": str(job.get("pubDate") or "")[:19]})
    except Exception as exc:
        failed += 1
        logger.warning("Jobicy could not be read: %s", exc)
    try:
        for offset in (0, 20, 40):
            answer = requests.get(BOARD_4, params={"limit": 20, "offset": offset}, timeout=40, headers=headers)
            answer.raise_for_status()
            for job in answer.json().get("jobs") or []:
                if not isinstance(job, dict) or not job.get("guid"):
                    continue
                stamp, places = job.get("pubDate"), job.get("locationRestrictions")
                found.append({"source": "himalayas", "id": hashlib.sha1(str(job["guid"]).encode("utf-8")).hexdigest()[:24], "title": job.get("title"),
                              "description": job.get("description") or job.get("excerpt"), "tags": job.get("categories") if isinstance(job.get("categories"), list) else [],
                              "company_name": job.get("companyName"), "candidate_required_location": ", ".join(places) if isinstance(places, list) else "",
                              "url": job.get("applicationLink") or job.get("guid"),
                              "publication_date": datetime.utcfromtimestamp(int(stamp)).isoformat() if str(stamp or "").isdigit() else ""})
            time.sleep(1)
    except Exception as exc:
        failed += 1
        logger.warning("Himalayas could not be read: %s", exc)
    if failed == 4:
        raise RuntimeError("no job board could be reached")
    return found


def last_read(db: Session) -> datetime | None:
    """When the job boards were last read, whether or not they had anything new. None: never, with both boards."""
    from app.services.settings_service import get_settings

    marked = getattr(get_settings(db), "market_read_at", None)
    return marked.replace(tzinfo=None) if marked is not None else None


def read_public(db: Session, now: datetime | None = None) -> dict:
    """Read the public job board and keep the offers of the fields taught here.
    Returns {"state": "read" | "recent" | "unreachable", "new", "kept"}; it is read at most every six hours."""
    now = now or datetime.utcnow()
    before = last_read(db)
    if before is not None and now - before < READ_AGAIN_AFTER:
        return {"state": "recent", "new": 0, "kept": db.query(MarketOffer).filter(MarketOffer.source.in_(BOARDS)).count()}
    try:
        jobs = fetch_public()
    except Exception as exc:
        logger.warning("The job board could not be read: %s", exc)
        return {"state": "unreachable", "new": 0, "kept": 0}
    known = {(row.source, row.external_id) for row in db.query(MarketOffer.source, MarketOffer.external_id).filter(MarketOffer.source.in_(BOARDS)).all()}
    new = 0
    for job in jobs:
        if not isinstance(job, dict) or not job.get("id") or not job.get("title"):
            continue
        source = job.get("source") if job.get("source") in BOARDS else "remotive"
        key, title = str(job["id"])[:200], str(job["title"])[:200]
        text = plain(f"{job.get('description') or ''} {' '.join(str(tag) for tag in job.get('tags') or [])}")
        if (source, key) in known or not (fields_of(title, f"{title}. {text}") or others_of(title)):       # only offers of the trade
            continue
        posted = None
        try:
            posted = datetime.fromisoformat(str(job.get("publication_date") or "")[:19])
        except ValueError:
            pass
        db.add(MarketOffer(source=source, external_id=key, title=title, company=str(job.get("company_name") or "")[:120],
                           location=str(job.get("candidate_required_location") or "")[:120], url=str(job.get("url") or "")[:400],
                           text=text, posted_at=posted, added_at=now))
        known.add((source, key))
        new += 1
    from app.services.settings_service import get_settings

    get_settings(db).market_read_at = now
    db.commit()
    # Not kept for ever: the oldest offers of the boards leave when there are too many.
    extra = db.query(MarketOffer).filter(MarketOffer.source.in_(BOARDS)).count() - KEEP_OFFERS
    if extra > 0:
        for row in db.query(MarketOffer).filter(MarketOffer.source.in_(BOARDS)).order_by(MarketOffer.added_at.asc(), MarketOffer.posted_at.asc()).limit(extra).all():
            db.delete(row)
        db.commit()
    return {"state": "read", "new": new, "kept": db.query(MarketOffer).filter(MarketOffer.source.in_(BOARDS)).count()}


def read_if_due(db: Session, now: datetime | None = None) -> dict | None:
    """The daily reading done by itself: None when the boards were read in the last 24 hours."""
    now = now or datetime.utcnow()
    before = last_read(db)
    if before is not None and now - before < READ_EVERY:
        return None
    return read_public(db, now=now)


def add_pasted(db: Session, text: str, title: str = "") -> MarketOffer | None:
    """An offer the admin copied (from LinkedIn or anywhere). None when there is nothing to read in it."""
    body = plain(text)
    if len(body) < 40:
        return None
    title = " ".join((title or "").split())[:200] or " ".join((text or "").strip().splitlines()[0].split())[:120] or "Pasted offer"
    key = hashlib.sha1(body.encode("utf-8")).hexdigest()[:20]
    row = db.query(MarketOffer).filter(MarketOffer.source == "pasted", MarketOffer.external_id == key).first()
    if row is None:
        row = MarketOffer(source="pasted", external_id=key, title=title, text=body, added_at=datetime.utcnow())
        db.add(row)
        db.commit()
    return row


# ------------------------------------------------------------------
# A program against the offers of its field
# ------------------------------------------------------------------

def program_text(db: Session, program: Program) -> str:
    """Everything the program says it teaches."""
    parts = [program.name or "", program.description or ""]
    for level in program.levels:
        parts += [level.title or "", level.description or ""]
        for week in level.weeks:
            parts += [week.topic_title or "", week.learning_objectives or ""]
    formation = db.query(WebsiteItem).filter(
        WebsiteItem.kind == "formation", WebsiteItem.program_id == program.id, WebsiteItem.gone_at.is_(None)).first()
    if formation is not None:
        details = formation.details
        parts += [str(item) for item in details.get("technologies") or []]
        parts += [str(week.get("content") or "") + " " + str(week.get("video_title") or "") for week in details.get("weeks") or [] if isinstance(week, dict)]
    return " ".join(parts)


def analysis(db: Session, program: Program, offers: list[dict] | None = None) -> dict:
    """What the offers of a program's field ask, what the program covers, and what to add."""
    subject = level_bank.subject_of(program.name, program.website_slug)
    found = {"subject": subject, "field": level_bank.SUBJECTS[subject][0] if subject else None, "offers": 0, "sources": {},
             "skills": [], "employers": [], "fit": None, "enough": False, "add": [], "content": [], "strengths": [], "advice": [], "latest": [],
             "list": [],
             "search": None}
    if subject is None:
        found["advice"] = ["This program's field is not one the offers are sorted by yet, so nothing can be compared."]
        return found
    offers = all_offers(db) if offers is None else offers
    mine = [offer for offer in offers if subject in offer["fields"]]
    total = len(mine)
    keywords = SEARCH[subject]
    found.update({
        "offers": total, "enough": total >= ENOUGH,
        "sources": {source: sum(1 for offer in mine if offer["source"] == source) for source in sorted({offer["source"] for offer in mine})},
        "latest": sorted(mine, key=lambda offer: offer["posted"] or datetime.min, reverse=True)[:5],
        "search": {
            "words": keywords,
            "linkedin": f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(keywords)}&location=Tunisia",
            "linkedin_world": f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(keywords)}",
        },
    })
    if not total:
        found["advice"] = ["No offer of this field has been read yet. Read the job market, or paste a few offers."]
        return found

    taught = program_text(db, program)
    covered = skills_in(taught, subject)
    counts: dict[str, int] = {}
    shared: dict[str, int] = {}
    for offer in mine:
        for name in skills_in(offer["text"], subject):
            counts[name] = counts.get(name, 0) + 1
        for name in common_in(offer["text"]):
            shared[name] = shared.get(name, 0) + 1
    skills = sorted(
        ({"label": name, "count": count, "pct": round(count / total * 100), "covered": name in covered} for name, count in counts.items()),
        key=lambda item: (-item["count"], item["label"]),
    )
    asked = [item for item in skills if item["pct"] >= 10][:14] or skills[:8]
    weight = sum(item["count"] for item in asked)
    found["skills"] = asked
    found["fit"] = round(sum(item["count"] for item in asked if item["covered"]) / weight * 100) if weight else None
    taught_common = common_in(taught)
    found["employers"] = sorted(
        ({"label": name, "pct": round(count / total * 100), "covered": name in taught_common} for name, count in shared.items()),
        key=lambda item: -item["pct"],
    )[:6]
    found["add"] = [item for item in asked if not item["covered"] and item["pct"] >= WORTH_ADDING][:5]
    found["strengths"] = [item for item in asked if item["covered"]][:4]
    # Each offer of the field with what was read in it: who, where, when, and the skills it names.
    for offer in sorted(mine, key=lambda item: item["posted"] or datetime.min, reverse=True)[:OFFERS_SHOWN]:
        named = sorted(skills_in(offer["text"], subject))
        found["list"].append({
            "title": offer["title"], "company": offer["company"], "location": offer["location"], "url": offer["url"], "source": offer["source"],
            "posted": offer["posted"], "taught": [name for name in named if name in covered], "missing": [name for name in named if name not in covered],
            "also": sorted(common_in(offer["text"])),
        })
    # What adding each missing skill means: every skill asked and not named, with what to teach.
    found["content"] = [dict(item, module=market_content.module_for(item["label"])) for item in asked if not item["covered"]][:CONTENT_IDEAS]

    advice = []
    for item in found["add"]:
        advice.append(f"Add {item['label']}: {item['pct']}% of the offers ask for it ({item['count']} of {total}) and the program does not name it.")
    if found["strengths"]:
        advice.append("Put forward what is already taught and most asked: "
                      + ", ".join(f"{item['label']} ({item['pct']}%)" for item in found["strengths"]) + ".")
    soft = [item for item in found["employers"] if not item["covered"] and item["pct"] >= 25]
    if soft:
        advice.append("Offers also ask for " + ", ".join(f"{item['label']} ({item['pct']}%)" for item in soft[:4])
                      + ": say in the program how candidates practise them.")
    if not found["add"] and found["fit"] is not None and found["fit"] >= 80:
        advice.insert(0, "The program already covers most of what the offers ask.")
    if not found["enough"]:
        advice.append(f"Only {total} offer{'s' if total != 1 else ''} of this field so far: take these percentages as a hint, and add more offers.")
    found["advice"] = advice
    return found


def trends(db: Session, offers: list[dict] | None = None) -> list[dict]:
    """Every field of the trade by the offers that ask for it, taught here or not, the most asked first."""
    offers = all_offers(db) if offers is None else offers
    sorted_offers = [offer for offer in offers if offer["fields"] or offer.get("others")]
    total = len(sorted_offers)
    if not total:
        return []
    programs: dict[str, list[str]] = {}
    for program in db.query(Program).order_by(Program.name.asc()).all():
        subject = level_bank.subject_of(program.name, program.website_slug)
        if subject:
            programs.setdefault(subject, []).append(program.name)

    def top(mine: list[dict], patterns) -> list[dict]:
        counts: dict[str, int] = {}
        for offer in mine:
            for name, pattern in patterns:
                if pattern.search(offer["text"]):
                    counts[name] = counts.get(name, 0) + 1
        ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:6]
        return [{"label": name, "pct": round(count / len(mine) * 100)} for name, count in ranked]

    rows = []
    for subject, (name, _) in level_bank.SUBJECTS.items():
        mine = [offer for offer in sorted_offers if subject in offer["fields"]]
        if mine:
            rows.append({"key": subject, "name": FIELD_NAMES.get(subject, name), "offers": len(mine), "pct": round(len(mine) / total * 100),
                         "taught": bool(programs.get(subject)), "programs": programs.get(subject, []), "closest": [],
                         "skills": top(mine, _SKILL[subject]), "enough": len(mine) >= ENOUGH})
    for key, (name, _, _, near) in OTHER.items():
        mine = [offer for offer in sorted_offers if key in offer.get("others", [])]
        if mine:
            closest = [title for subject in near for title in programs.get(subject, [])][:3]
            rows.append({"key": key, "name": name, "offers": len(mine), "pct": round(len(mine) / total * 100),
                         "taught": False, "programs": [], "closest": closest,
                         "skills": top(mine, _OTHER_SKILL[key]), "enough": len(mine) >= ENOUGH})
    rows.sort(key=lambda row: (-row["offers"], row["name"]))
    for row in rows:
        asked = ", ".join(f"{item['label']} {item['pct']}%" for item in row["skills"][:4])
        if row["taught"]:
            row["advice"] = "You teach it: " + ", ".join(row["programs"]) + "."
        elif not row["enough"]:
            row["advice"] = "No program here teaches it, and too few offers were read to say more."
        else:
            row["advice"] = (f"No program here teaches it. Most asked: {asked}. " if asked else "No program here teaches it. ") + (
                "It could start as a module in " + " or ".join(row["closest"]) + ", then become a program of its own."
                if row["closest"] else "It would be a new program.")
    return rows


def overview(db: Session) -> dict:
    """Every program against the market, and the offers behind the figures."""
    offers = all_offers(db)
    programs = []
    for program in db.query(Program).order_by(Program.name.asc()).all():
        result = analysis(db, program, offers)
        programs.append({"program": program, **result})
    unsorted = [offer for offer in offers if not offer["fields"] and not offer.get("others")]
    return {
        "programs": programs,
        "trends": trends(db, offers),
        "offers": offers,
        "latest": sorted(offers, key=lambda offer: offer["posted"] or datetime.min, reverse=True)[:12],
        "total": len(offers),
        "sources": {source: sum(1 for offer in offers if offer["source"] == source) for source in sorted({offer["source"] for offer in offers})},
        "unsorted": len(unsorted),
        "last_read": last_read(db),
        "can_read": last_read(db) is None or datetime.utcnow() - last_read(db) >= READ_AGAIN_AFTER,
        "pasted": [row for row in db.query(MarketOffer).filter(MarketOffer.source == "pasted").order_by(MarketOffer.added_at.desc()).all()],
    }
