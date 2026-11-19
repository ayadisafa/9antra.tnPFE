"""What could be enhanced, kept up to date by the market by itself (System › Control room › Suggestions).

A catalogue of what a training centre's application can offer (online payment, certificates, a space for each candidate,
community, career services…). For each one, counted, never guessed:
- how often the market talks about it: the journal's articles (training and edtech feeds, tech, marketing; read every 6
  hours), the last 30 days against the 30 before, with the articles themselves as evidence;
- how often the centre's own candidates ask for it in their messages (90 days);
- whether the application already has it.
The ranking follows: what is missing and talked about (or asked for) comes first, and it moves as the news moves.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.services.test_book import L, say
from app.services.ui_words import t as _t

RECENT = timedelta(days=30)
# The journal's fields that speak of the training market (code, security and general tech news would count
# “certificates”, “credentials” or shop payments of another kind).
MARKET_FIELDS = ("edtech", "marketing")
ASKED = timedelta(days=90)
SHOWN = 8
_WEIGHT = {"missing": 1.0, "part": 0.65, "have": 0.3}


def _rx(pattern: str) -> re.Pattern:
    return re.compile(r"(?<![\w])(?:" + pattern + r")(?![\w])", re.IGNORECASE)


# key, moving picture, motion, what it is, what it needs, effort, market words (articles: English and French),
# demand words (the candidates' messages: French, English, Tunisian, Arabic), whether the application has it.
CAPABILITIES = [
    ("online_payment", "💳", "pulse",
     L("Online payment", "Paiement en ligne", "الدفع عبر الإنترنت"),
     L("A link in the payment reminder to pay by card or D17; the payment is recorded and the class placed by itself.",
       "Un lien dans le rappel de paiement pour payer par carte ou D17 ; le paiement est enregistré et la classe placée d'elle-même.",
       "رابط في تذكير الدفع للدفع بالبطاقة أو D17؛ يُسجل الدفع ويُوضع المترشح في القسم تلقائيا."),
     L("An account with a Tunisian payment provider (Konnect, Flouci, Paymee) and its keys.",
       "Un compte chez un prestataire de paiement tunisien (Konnect, Flouci, Paymee) et ses clés.", "حساب لدى مزود دفع تونسي (Konnect، Flouci، Paymee) ومفاتيحه."),
     "medium",
     r"online payments?|payment links?|pay online|checkout|payment gateways?|e-?payments?|mobile money|digital wallets?|paiements? en ligne|paiement mobile|"
     r"konnect|flouci|paymee|d17",
     r"payer en ligne|paiement en ligne|par carte|carte bancaire|d17|flouci|konnect|pay online|credit card|card payment|بطاقة بنكية|الدفع الإلكتروني|نخلص بالكارت",
     "missing"),
    ("certificates", "🎓", "twinkle",
     L("Certificates", "Certificats", "الشهادات"),
     L("A PDF certificate with a QR code anyone can check, sent when a class finishes.", "Un certificat PDF avec un QR code vérifiable, envoyé à la fin d'une classe.",
       "شهادة PDF برمز QR قابلة للتحقق، تُرسل عند انتهاء القسم."),
     L("Your model of certificate (logo, signature, wording).", "Votre modèle de certificat (logo, signature, texte).", "نموذج شهادتك (الشعار، التوقيع، النص)."),
     "small",
     r"certificat\w*|certificates?|micro-?credentials?|digital credentials?|digital badges?|open badges?|accredit\w*|accrédit\w*|attestations?",
     r"certificat\w*|attestation\w*|dipl[oô]mes?|certificates?|chahada|chhada|شهادة|الشهادة",
     "missing"),
    ("portal", "🪪", "float",
     L("A space for each candidate", "Un espace pour chaque candidat", "فضاء لكل مترشح"),
     L("Their sessions, payments, files, questions and certificate in one page, from a personal link.",
       "Ses séances, paiements, fichiers, questions et certificat sur une page, depuis un lien personnel.", "حصصه ودفعاته وملفاته وأسئلته وشهادته في صفحة واحدة برابط شخصي."),
     L("A public address for the application.", "Une adresse publique pour l'application.", "عنوان عام للتطبيق."),
     "large",
     r"learner portals?|student portals?|learning platforms?|lms|learning management systems?|espace (?:apprenant|étudiant|etudiant)|plateformes? d'apprentissage",
     r"mon espace|espace candidat|mon compte|ma plateforme|my account|login|حسابي",
     "missing"),
    ("installments", "🧾", "bounce",
     L("Paying in instalments", "Payer en plusieurs fois", "الدفع بالتقسيط"),
     L("Monthly prices exist; instalments could also be offered on any program, with their reminders.",
       "Les prix au mois existent ; le paiement en plusieurs fois pourrait aussi être proposé sur toute formation, avec ses rappels.",
       "الأسعار الشهرية موجودة؛ ويمكن عرض التقسيط على كل برنامج مع تذكيراته."),
     L("Your rule: how many parts, when.", "Votre règle : combien de parts, quand.", "قاعدتك: كم قسطا ومتى."),
     "small",
     r"instal?lments?|buy now,? pay later|bnpl|payment plans?|income share agreements?|paiement en plusieurs fois|échelonn\w*|facilités de paiement",
     r"tranches?|plusieurs fois|deux fois|trois fois|facilit\w*|ta9sit|taqsit|taksit|تقسيط|installments?|payment plan",
     "part"),
    ("recordings", "🎬", "pulse",
     L("Session recordings", "Enregistrements des séances", "تسجيلات الحصص"),
     L("Each online session recorded and kept with its week, for those who missed it.", "Chaque séance en ligne enregistrée et gardée avec sa semaine, pour les absents.",
       "تسجيل كل حصة عن بعد وحفظها مع أسبوعها، للغائبين."),
     L("A meeting service that records (Jitsi as a Service, Zoom) or recordings uploaded by the professors.",
       "Un service de réunion qui enregistre (Jitsi as a Service, Zoom) ou des enregistrements déposés par les professeurs.",
       "خدمة اجتماعات تسجل (Jitsi as a Service، Zoom) أو تسجيلات يرفعها الأساتذة."),
     "medium",
     r"recorded (?:lectures|sessions|classes)|on-demand (?:video|courses?)|replays?|video lessons?|asynchronous|enregistrements? (?:des|de) (?:cours|séances)|vidéos? à la demande|asynchrone",
     r"enregistrement\w*|replay|rediffusion|vid[ée]o de la s[ée]ance|recording|تسجيل الحصة|tsjil",
     "part"),
    ("community", "👥", "wave",
     L("A community of learners", "Une communauté d'apprenants", "مجتمع للمتعلمين"),
     L("A group per class and one for alumni, led by the centre: questions, events, offers.", "Un groupe par classe et un pour les anciens, animés par le centre : questions, événements, offres.",
       "مجموعة لكل قسم وأخرى للقدامى يديرها المركز: أسئلة وفعاليات وعروض."),
     L("Where the groups live (WhatsApp communities, Discord, or inside the application).", "Où vivent les groupes (communautés WhatsApp, Discord, ou dans l'application).",
       "مكان المجموعات (مجتمعات واتساب، Discord، أو داخل التطبيق)."),
     "medium",
     r"learning communit\w*|cohort-based|cohorts?|peer learning|alumni|community-based learning|communaut\w* d'apprenants|apprentissage entre pairs|anciens élèves",
     r"communaut\w*|community|discord|telegram|groupe whatsapp|groupe telegram",
     "missing"),
    ("career", "💼", "tilt",
     L("Career services", "Accompagnement vers l'emploi", "المرافقة نحو التشغيل"),
     L("CV and interview help, internships and job offers matched to each graduate, with the companies you know.",
       "Aide au CV et à l'entretien, stages et offres d'emploi proposés à chaque diplômé, avec les entreprises que vous connaissez.",
       "مساعدة في السيرة الذاتية والمقابلات، وتربصات وعروض عمل لكل متخرج مع الشركات التي تعرفها."),
     L("A list of partner companies and your yes to send them profiles.", "Une liste d'entreprises partenaires et votre accord pour leur envoyer des profils.",
       "قائمة بالشركات الشريكة وموافقتك على إرسال الملفات إليها."),
     "medium",
     r"job placements?|career services?|career support|hiring partners?|job guarantee|internships?|employability|job-ready|insertion professionnelle|employabilit\w*|alternance",
     r"stage|emploi|recrutement|job|internship|خدمة|تربص|khedma|tarbos",
     "missing"),
    ("gamification", "🏆", "twinkle",
     L("Points and badges", "Points et badges", "النقاط والشارات"),
     L("Points for presence and answers, badges and a class ranking, to keep candidates going.",
       "Des points pour la présence et les réponses, des badges et un classement de classe, pour garder l'élan.",
       "نقاط للحضور والإجابات، وشارات وترتيب للقسم، للحفاظ على الحماس."),
     L("Nothing: it can be built from the presence and the answers already counted.", "Rien : cela peut être fait avec la présence et les réponses déjà comptées.",
       "لا شيء: يمكن بناؤه من الحضور والإجابات المحسوبة."),
     "small",
     r"gamif\w*|ludifi\w*|leaderboards?|streaks?|points and rewards",
     None, "missing"),
    ("microlearning", "⏱️", "spin",
     L("Short lessons", "Leçons courtes", "دروس قصيرة"),
     L("Five-minute lessons and quizzes between sessions, sent on WhatsApp or by email.", "Des leçons et quiz de cinq minutes entre les séances, envoyés sur WhatsApp ou par email.",
       "دروس واختبارات من خمس دقائق بين الحصص، تُرسل عبر واتساب أو البريد."),
     L("The professors' course files (already used for the weekly questions).", "Les fichiers de cours des professeurs (déjà utilisés pour les questions de la semaine).",
       "ملفات دروس الأساتذة (المستعملة لأسئلة الأسبوع)."),
     "small",
     r"micro-?learning|micro-?apprentissage|bite-sized|short lessons?|nano-?degrees?|capsules?",
     None, "missing"),
    ("b2b", "🏢", "float",
     L("Training for companies", "Formation pour les entreprises", "التكوين للشركات"),
     L("Offers for companies: their teams trained on site or online, with an invoice and attendance reports.",
       "Des offres pour les entreprises : leurs équipes formées sur place ou en ligne, avec facture et rapports de présence.",
       "عروض للشركات: تكوين فرقها حضوريا أو عن بعد، مع فاتورة وتقارير حضور."),
     L("Your company prices and an invoice model.", "Vos prix entreprise et un modèle de facture.", "أسعارك للشركات ونموذج فاتورة."),
     "medium",
     r"corporate training|upskilling|reskilling|employee training|workforce (?:training|development)|learning and development|l&d|formation (?:professionnelle|continue|en entreprise)|montée en compétences",
     r"entreprise|soci[ée]t[ée]|notre [ée]quipe|mon [ée]quipe|company|corporate|for my team|شركة",
     "missing"),
    ("projects", "🛠️", "bounce",
     L("Real projects and hackathons", "Projets réels et hackathons", "مشاريع حقيقية وهاكاثونات"),
     L("A project per program, shown in a portfolio page, and a hackathon each term.", "Un projet par formation, montré dans une page portfolio, et un hackathon par trimestre.",
       "مشروع لكل برنامج يُعرض في صفحة أعمال، وهاكاثون كل فصل."),
     L("Subjects from the professors, and partners for the prizes.", "Des sujets des professeurs, et des partenaires pour les prix.", "مواضيع من الأساتذة وشركاء للجوائز."),
     "medium",
     r"project-based|hands-on projects?|capstones?|student portfolios?|hackathons?|apprentissage par projets?",
     r"projet|project|portfolio|hackathon|مشروع",
     "missing"),
    ("personal_path", "🧭", "spin",
     L("A path for each candidate", "Un parcours pour chaque candidat", "مسار لكل مترشح"),
     L("After the level check, extra exercises or a faster pace chosen for each one.", "Après le test de niveau, des exercices en plus ou un rythme plus rapide choisis pour chacun.",
       "بعد اختبار المستوى، تمارين إضافية أو نسق أسرع لكل واحد."),
     L("Nothing: the level check already gives the level.", "Rien : le test de niveau donne déjà le niveau.", "لا شيء: اختبار المستوى يعطي المستوى."),
     "medium",
     r"personali[sz]ed learning|adaptive learning|learning paths?|parcours personnalis\w*|apprentissage adaptatif",
     None, "part"),
    ("qr_presence", "📷", "pulse",
     L("Presence by QR code", "Présence par QR code", "الحضور برمز QR"),
     L("Candidates scan the class's code when they arrive on site; presence is marked by itself.", "Les candidats scannent le code de la classe en arrivant ; la présence se marque d'elle-même.",
       "يمسح المترشحون رمز القسم عند الوصول؛ ويُسجل الحضور تلقائيا."),
     L("A public address, or the centre's Wi-Fi.", "Une adresse publique, ou le Wi-Fi du centre.", "عنوان عام، أو شبكة الواي فاي للمركز."),
     "small",
     r"qr codes? attendance|attendance tracking|smart attendance|pr[ée]sence par qr|émargement (?:numérique|électronique)",
     None, "missing"),
    ("ai_tutor", "🤖", "float",
     L("An AI assistant for learners", "Un assistant IA pour les apprenants", "مساعد ذكاء اصطناعي للمتعلمين"),
     L("Answers questions about the course and the centre, day and night.", "Répond aux questions sur le cours et le centre, jour et nuit.", "يجيب عن أسئلة الدرس والمركز ليلا ونهارا."),
     L("Already there: the email assistant and Yasmine; say it in your posts.", "Déjà là : l'assistant des emails et Yasmine ; dites-le dans vos posts.",
       "موجود: مساعد البريد وياسمين؛ قل ذلك في منشوراتك."),
     "small",
     r"ai tutors?|ai teaching assistants?|ai coach\w*|ai-powered learning|chatbots? (?:for|in) (?:learning|education)|tuteurs? ia|assistants? ia",
     None, "have"),
    ("live_online", "🖥️", "pulse",
     L("Live online classes", "Classes en ligne en direct", "أقسام مباشرة عن بعد"),
     L("Online and mixed classes with a personal link for each candidate.", "Des classes en ligne et mixtes avec un lien personnel pour chaque candidat.",
       "أقسام عن بعد ومختلطة مع رابط شخصي لكل مترشح."),
     L("Already there; candidates can only open the links once the application has a public address.",
       "Déjà là ; les candidats n'ouvrent les liens qu'une fois que l'application a une adresse publique.", "موجود؛ ولا يفتح المترشحون الروابط إلا عندما يكون للتطبيق عنوان عام."),
     "small",
     r"virtual classrooms?|live online|hybrid learning|remote learning|online bootcamps?|classes? virtuelles?|formation à distance|apprentissage hybride|formation hybride",
     r"en ligne|[àa] distance|online|remote|عن بعد|3an bo3d",
     "have"),
    ("mobile_app", "📱", "bounce",
     L("A phone app for candidates", "Une app mobile pour les candidats", "تطبيق هاتف للمترشحين"),
     L("The staff already have the phone app; candidates could get theirs, with notifications.", "L'équipe a déjà l'app sur téléphone ; les candidats pourraient avoir la leur, avec notifications.",
       "للفريق تطبيق الهاتف؛ ويمكن للمترشحين الحصول على تطبيقهم مع الإشعارات."),
     L("The space for each candidate, and a public address.", "L'espace pour chaque candidat, et une adresse publique.", "فضاء لكل مترشح وعنوان عام."),
     "large",
     r"mobile learning|m-learning|learning apps?|apprentissage mobile",
     r"application mobile|mobile app",
     "part"),
    ("referral", "🤝", "wave",
     L("Refer a friend", "Parrainage", "دعوة صديق"),
     L("A personal code for each candidate: a friend who registers gives both a discount.", "Un code personnel pour chaque candidat : un ami qui s'inscrit donne une réduction aux deux.",
       "رمز شخصي لكل مترشح: الصديق الذي يسجل يمنح تخفيضا للاثنين."),
     L("Your rule for the discount.", "Votre règle pour la réduction.", "قاعدتك للتخفيض."),
     "small",
     r"referral programs?|refer-a-friend|ambassador programs?|word of mouth|parrainage|bouche-à-oreille",
     r"parrain\w*|mon ami|ma copine|mon copain|un ami|a friend|my friend|sa7bi|صاحبي",
     "missing"),
]
_COMPILED = {key: (_rx(market), _rx(asked) if asked else None) for key, _, _, _, _, _, _, market, asked, _ in CAPABILITIES}
STATES = {"missing": "Missing", "part": "Partly there", "have": "You have it"}
EFFORTS = {"small": "Small", "medium": "Medium", "large": "Large"}


def _have(db: Session, key: str, default: str) -> str:
    """Whether the application has it, read from the records where it can be."""
    try:
        if key == "installments":
            from app.models import Registration

            return "part" if db.query(Registration.id).filter(Registration.month_price.isnot(None)).first() else "missing"
    except Exception:
        return default
    return default


def ranking(db: Session, now: datetime | None = None) -> dict:
    """{"ideas": the ranked missing or partial ones (with evidence), "have": what the market talks about that the centre
    already has, "quiet": watched without a signal yet, "articles", "read_at"}."""
    from app.models.candidate_email import CandidateEmail
    from app.models.news_item import NewsItem
    from app.services import journal

    now = now or datetime.utcnow()
    news = db.query(NewsItem.title, NewsItem.summary, NewsItem.url, NewsItem.source, NewsItem.published_at).filter(
        NewsItem.published_at >= now - 2 * RECENT, NewsItem.domain.in_(MARKET_FIELDS)).all()
    asked = [body for (body,) in db.query(CandidateEmail.body).filter(CandidateEmail.direction == "inbound", CandidateEmail.received_at >= now - ASKED).all()]
    found = []
    for key, emoji, motion, title, what, needs, effort, _, _, default in CAPABILITIES:
        market, demand = _COMPILED[key]
        recent, older, examples = 0, 0, []
        for row in news:
            if market.search(f"{row.title} {row.summary or ''}"):
                when = row.published_at or now
                if when >= now - RECENT:
                    recent += 1
                else:
                    older += 1
                examples.append(row)
        wanted = sum(1 for body in asked if demand is not None and body and demand.search(body))
        state = _have(db, key, default)
        score = _WEIGHT[state] * (2.0 * math.log1p(recent) + math.log1p(older) + 2.5 * math.log1p(wanted))
        trend = ("up" if recent >= 2 and recent >= 1.3 * max(older, 1) else "down" if older >= 2 and recent <= 0.7 * older else "steady")
        examples.sort(key=lambda row: row.published_at or now, reverse=True)
        facts = []
        if recent or older:
            facts.append({"icon": "bi-newspaper", "text": _t("Articles that name it in 60 days: {0} (in the last 30: {1}).", recent + older, recent)})
        if wanted:
            facts.append({"icon": "bi-chat-dots", "text": _t("Messages of your candidates asking for it in 90 days: {0}.", wanted)})
        found.append({"key": key, "emoji": emoji, "motion": motion, "title": say(title), "what": say(what), "needs": say(needs),
                      "effort": _t(EFFORTS[effort]), "state": state, "state_label": _t(STATES[state]), "score": round(score, 2), "trend": trend,
                      "recent": recent, "older": older, "asked": wanted, "facts": facts,
                      "examples": [{"title": row.title, "url": row.url, "source": row.source} for row in examples[:3]]})
    found.sort(key=lambda item: -item["score"])
    ideas = [item for item in found if item["state"] != "have" and item["score"] > 0]
    return {"ideas": ideas[:SHOWN], "more": ideas[SHOWN:], "have": [item for item in found if item["state"] == "have" and item["score"] > 0],
            "quiet": [item for item in found if item["score"] == 0], "articles": len(news), "read_at": journal.last_read(db)}
