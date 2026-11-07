"""Yasmine shows the application around, page after page.

"Show me around" in her bubble starts it: she opens each page in turn, says in one sentence what it is for, offers its
spotlight tour (page_tours, the "?" of the page), then leads to the next page (its menu link pulses). Where the visit
is, is kept in the browser (localStorage "grand-tour"), so it goes on from one page to the next. Said in the four
languages she speaks.
"""
from __future__ import annotations

# (path, {lang: (name, what she says there)})
STEPS = [
    ("/", {
        "fr": ("Tableau de bord", "Le tableau de bord : en haut, ce qui vous attend aujourd'hui ; le reste en quatre onglets."),
        "en": ("Dashboard", "The dashboard: what waits for you today at the top, the rest in four tabs."),
        "ar": ("لوحة القيادة", "لوحة القيادة: ما ينتظرك اليوم في الأعلى، والباقي في أربع علامات تبويب."),
        "tn": ("Dashboard", "El dashboard: el fou9 chnowa yestanna fik lyoum, w el ba9i fi arba3 onglets.")}),
    ("/inbox", {
        "fr": ("Boîte de réception", "La boîte de réception : ce qui a besoin de vous d'abord, puis ce qui n'attend pas de réponse, puis toutes les conversations."),
        "en": ("Inbox", "The inbox: what needs you first, then what needs no reply, then every conversation."),
        "ar": ("صندوق الرسائل", "صندوق الرسائل: ما يحتاجك أولا، ثم ما لا يحتاج ردا، ثم كل المحادثات."),
        "tn": ("Inbox", "El inbox: elli yest7a9ek l'awel, ba3d elli ma yest7a9ch jweb, ba3d el conversations el kol.")}),
    ("/candidate/all", {
        "fr": ("Candidats", "Les candidats : chacun avec ses inscriptions, ses paiements, ses messages et ses avis."),
        "en": ("Candidates", "The candidates: each with their registrations, payments, messages and reviews."),
        "ar": ("المترشحون", "المترشحون: كل واحد بتسجيلاته ومدفوعاته ورسائله وآرائه."),
        "tn": ("Candidats", "El candidats: kol wa7ed b'inscriptions mte3ou, khlasou, messages mte3ou w ra2you.")}),
    ("/classes/", {
        "fr": ("Classes", "Les classes : leurs séances, la présence, et ce que les candidats en pensent (avec le QR code de la dernière séance)."),
        "en": ("Classes", "The classes: their sessions, attendance, and what the candidates think (with the QR code of the last session)."),
        "ar": ("الأقسام", "الأقسام: حصصها، والحضور، ورأي المترشحين (مع رمز QR للحصة الأخيرة)."),
        "tn": ("Classes", "El classes: les séances, el présence, w chnowa ygoulou el candidats (m3a el QR code mta3 e5er séance).")}),
    ("/payments", {
        "fr": ("Paiements", "Les paiements : ce qui est payé, ce qui reste, et les preuves envoyées à vérifier."),
        "en": ("Payments", "Payments: what is paid, what is left, and the proofs sent to check."),
        "ar": ("المدفوعات", "المدفوعات: ما دُفع، وما بقي، وإثباتات الدفع للتثبت منها."),
        "tn": ("Paiements", "El paiements: chnowa tkhallas, chnowa b9a, w el preuves elli lazem tchoufhom.")}),
    ("/reminders", {
        "fr": ("Rappels", "Les rappels : ceux qui partent seuls, et ceux que vous programmez."),
        "en": ("Reminders", "Reminders: the ones that go by themselves, and the ones you plan."),
        "ar": ("التذكيرات", "التذكيرات: التي تُرسل وحدها، والتي تبرمجها أنت."),
        "tn": ("Rappels", "El rappels: elli yemchiw wa7adhom, w elli t7ottehom enti.")}),
    ("/social", {
        "fr": ("Réseaux sociaux", "Les réseaux sociaux : des publications prêtes, en tounsi, des vidéos, et les réussites à raconter."),
        "en": ("Social media", "Social media: ready posts in Tunisian, videos, and the successes worth telling."),
        "ar": ("وسائل التواصل", "وسائل التواصل: منشورات جاهزة بالتونسي، وفيديوهات، والنجاحات التي تستحق أن تُروى."),
        "tn": ("Réseaux sociaux", "El réseaux sociaux: publications 7adhrin bel tounsi, vidéos, w el nja7at elli ylzem tet7ka.")}),
    ("/settings", {
        "fr": ("Paramètres", "Les paramètres : votre centre, les envois, l'équipe. Et je peux en changer beaucoup si vous me le demandez."),
        "en": ("Settings", "Settings: your centre, how messages go, the team. I can change many of them if you ask me."),
        "ar": ("الإعدادات", "الإعدادات: مركزك، والإرسال، والفريق. ويمكنني تغيير الكثير منها إن طلبت."),
        "tn": ("Paramètres", "El paramètres: el centre mte3ek, el envois, l'équipe. Najjem nbaddel barcha menhom ken t9olli.")}),
]
WORDS = {
    "fr": {"start": "Faites-moi visiter", "show": "Montre-moi cette page", "next": "Page suivante : {name}", "stop": "Arrêter la visite",
           "done": "C'est tout ! Vous connaissez maintenant chaque partie. Je reste là si vous avez besoin de moi.", "step": "Visite {i}/{n}"},
    "en": {"start": "Show me around", "show": "Show me this page", "next": "Next page: {name}", "stop": "Stop the visit",
           "done": "That's all! You now know every part. I'm here if you need me.", "step": "Visit {i}/{n}"},
    "ar": {"start": "عرّفيني بالتطبيق", "show": "أريني هذه الصفحة", "next": "الصفحة التالية: {name}", "stop": "إيقاف الجولة",
           "done": "هذا كل شيء! أنت تعرف الآن كل جزء. أنا هنا إن احتجتني.", "step": "الجولة {i}/{n}"},
    "tn": {"start": "Warrini el application", "show": "Warrini el page hedhi", "next": "El page el jeya: {name}", "stop": "Ywa9ef el visite",
           "done": "Hedha kol chay! Tawa ta3ref kol parti. Ana lehna ken tst7a99ni.", "step": "Visite {i}/{n}"},
}


def steps(lang: str) -> list[dict]:
    """[{"path", "name", "say"}] in her language."""
    lang = lang if lang in WORDS else "fr"
    return [{"path": path, "name": said[lang][0], "say": said[lang][1]} for path, said in STEPS]
