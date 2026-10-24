"""Tunisian Arabic (derja) as people type it: in Latin letters with numbers (3 = ع, 7 = ح, 9 = ق, 5 = خ, 8 = غ, 2 = ء)
or in Arabic letters, often mixed with French and English.

The local model reads derja poorly. Before it rewrites a note or reads a message, the derja words found are given to
it with their meaning: the words come from this list, written by hand, never guessed. Words that are also French or
English ("le", "win" in an English sentence...) are left out of the list on purpose.
"""
from __future__ import annotations

import re
import unicodedata

# meaning in French, in English: every way of writing the word
_WORDS: list[tuple[str, str, tuple[str, ...]]] = [
    ("bonjour", "hello", ("aslema", "3aslema", "aslama", "3aslama", "ahla", "ahlan", "عسلامة", "اهلا")),
    ("ça va", "fine / how are you", ("labes", "labas", "labess", "chnahwalek", "chna7walek", "chniya ahwalek", "لاباس", "شنحوالك")),
    ("merci", "thank you", ("ya3tik essa7a", "ya3tik sa7a", "y3aychek", "ya3aychek", "3aychek", "yaychek", "barkallahoufik", "berkallahoufik", "يعيشك", "يعطيك الصحة")),
    ("au revoir", "goodbye", ("bslama", "beslama", "bislema", "bisslema", "بالسلامة")),
    ("si Dieu le veut", "hopefully", ("inchallah", "nchallah", "inshallah", "nshallah", "ان شاء الله", "نشالله")),
    ("d'accord", "OK", ("behi", "bahi", "mrigel", "mrigl", "mriguel", "mrigla", "باهي", "مريڨل")),
    ("oui", "yes", ("ey", "eyh", "ih", "iyeh", "ayh", "ايه")),
    ("je", "I", ("ena", "ana", "انا")),
    ("tu", "you", ("enti", "inti", "enta", "انتي", "انت")),
    ("nous", "we", ("a7na", "ahna", "احنا")),
    ("quoi", "what", ("chnowa", "chnoua", "chnouwa", "chnou", "chneya", "chniya", "chnia", "شنوة", "شنية")),
    ("pourquoi", "why", ("3lech", "alech", "3leh", "3lach", "علاش")),
    ("comment", "how", ("kifech", "kifach", "kifeh", "kifèch", "كيفاش")),
    ("où", "where", ("win", "wen", "wein", "وين")),
    ("quand", "when", ("waqtech", "wa9tech", "wakteh", "wa9teh", "wa9tach", "imta", "وقتاش")),
    ("l'heure", "the time", ("wa9t", "wa9et", "wakt", "waqt", "وقت")),
    ("combien", "how much", ("9adech", "qadech", "9addech", "kadech", "9adeh", "9adach", "قداش")),
    ("qui", "who", ("chkoun", "chkon", "شكون")),
    ("il y a", "there is", ("famma", "fama", "فما")),
    ("il n'y a pas", "there is no", ("mafamma", "mafamech", "mafama", "mafamach", "مافماش")),
    ("je veux", "I want", ("nheb", "n7eb", "nhab", "n7ab", "nhebb", "نحب")),
    ("tu veux", "you want", ("t7eb", "theb", "t7ab", "تحب")),
    ("je peux", "I can", ("nejjem", "najjem", "nnajem", "nejem", "njem", "نجم")),
    ("tu peux", "you can", ("tnajem", "tnejjem", "tnajjem", "تنجم")),
    ("je sais", "I know", ("na3ref", "na3rf", "na3raf", "نعرف")),
    ("je ne sais pas", "I don't know", ("man3refch", "man3rafch", "ma na3refch", "منعرفش")),
    ("j'ai compris", "I understood", ("fhemt", "fhamt", "فهمت")),
    ("je n'ai pas compris", "I did not understand", ("mafhemtch", "mafhamtech", "ma fhemtech", "مافهمتش")),
    ("j'envoie", "I send", ("nab3ath", "nob3eth", "nab3eth", "nabaath", "نبعث")),
    ("envoie", "send", ("ab3ath", "ob3eth", "ab3eth", "ابعث", "ab3athli", "ab3ethli", "eb3ath", "eb3athli", "b3ath", "b3athli",
                        "ab3atli", "ab3etli", "ab3a4", "ab3a4li", "ابعثلي", "إبعث", "بعث")),
    ("dis-lui", "tell him", ("kolo", "kollo", "kolou", "kollou", "9olo", "9ollo", "9olou", "9ollou", "9oulou", "9oulo", "goulou", "gollo",
                             "qolo", "qollo", "kolha", "kollha", "9olha", "9ollha", "9oulha", "قلو", "قولو", "قلّو", "قلها", "قولها")),
    ("dis-leur", "tell them", ("kolhom", "kollhom", "9olhom", "9ollhom", "9oulhom", "قلهم", "قولهم")),
    ("venez", "come", ("ija", "iji", "ijini", "إجا", "ايجا", "اجا")),
    ("qu'il vienne", "that he comes", ("yji", "yjina", "يجي")),
    ("annulée", "cancelled", ("tna7at", "tna7a", "tnahat", "tna7aw", "tnahaw", "تنحات", "تنحى")),
    ("apporte", "bring", ("tjib", "jib", "yjib", "tjibou", "jibou", "تجيب", "جيب")),
    ("le", "the", ("el", "l'")),
    ("son", "his, her", ("mte3ha", "mte3ou", "mta3ha", "mta3ou", "mte3o", "mta3o", "متاعها", "متاعو")),
    ("leur", "their", ("mte3hom", "mta3hom", "متاعهم")),
    ("ton", "your", ("mte3ek", "mta3ek", "mte3k", "متاعك")),
    ("mon", "my", ("mte3i", "mta3i", "متاعي")),
    ("j'ai envoyé", "I sent", ("b3atht", "ba3atht", "b3ath", "بعثت")),
    ("j'ai payé", "I paid", ("khallast", "5allast", "khlast", "5last", "khalast", "5alast", "خلصت")),
    ("je paie", "I pay", ("nkhalles", "n5alles", "nkhales", "n5ales", "نخلص")),
    ("payer", "to pay", ("tkhalles", "t5alles", "khlas", "5las", "تخلص")),
    ("j'ai viré l'argent", "I transferred the money", ("7awelt", "hawelt", "7awalt", "hawalt", "حولت")),
    ("je viens", "I come", ("nji", "njik", "نجي")),
    ("tu viens", "you come", ("tji", "تجي")),
    ("je me suis inscrit", "I registered", ("tsajalt", "tsajjalt", "sajalt", "tsajelt", "تسجلت")),
    ("je m'inscris", "I register", ("nsajel", "nsajjel", "nsajjal", "نسجل")),
    ("s'inscrit", "registers", ("yetsajel", "yetsajjel", "yetsejjel", "yitsajjel", "yetsajal", "يتسجل", "يتسجّل")),
    ("j'attends", "I am waiting", ("nestanna", "nistanna", "نستنى")),
    ("attends", "wait", ("stanna", "estanna", "استنى")),
    ("dis-moi", "tell me", ("9olli", "9oulli", "qolli", "goulli", "9oli", "قلي")),
    ("réponds-moi", "answer me", ("jawebni", "jaweb", "rodd", "ردلي", "جاوبني")),
    ("je te réponds", "I will answer you", ("nraj3ek", "nraja3lek", "nrodlek", "نرجعلك")),
    ("je n'ai pas", "I do not have", ("ma3andich", "ma3andech", "ma3ndich", "معنديش")),
    ("j'ai", "I have", ("3andi", "3ndi", "عندي")),
    ("tu as", "you have", ("3andek", "3ndek", "عندك")),
    ("il faut", "must", ("lazem", "lezem", "lazim", "لازم")),
    ("pas", "not", ("mouch", "mich", "moch", "mech", "موش", "مش")),
    ("beaucoup", "a lot", ("barcha", "barsha", "yasser", "yesser", "برشا", "ياسر")),
    ("un peu", "a little", ("chwaya", "chwaye", "shwaya", "chouaya", "شوية")),
    ("maintenant", "now", ("taw", "tawa", "tawwa", "tw", "توا")),
    ("après", "after", ("ba3d", "ba3ed", "بعد")),
    ("avant", "before", ("9bal", "qbal", "gbal", "قبل")),
    ("demain", "tomorrow", ("ghodwa", "ghudwa", "8odwa", "ghodwe", "غدوة")),
    ("aujourd'hui", "today", ("lyoum", "el youm", "elyoum", "lyouma", "اليوم")),
    ("hier", "yesterday", ("lbere7", "lbera7", "el bareh", "lbareh", "البارح")),
    ("semaine", "week", ("jem3a", "jom3a", "joum3a", "جمعة")),
    ("mois", "month", ("chhar", "chahr", "شهر")),
    ("matin", "morning", ("sbe7", "sba7", "sbah", "صباح")),
    ("après-midi", "afternoon", ("3chiya", "3chia", "عشية")),
    ("argent", "money", ("flous", "flouss", "فلوس")),
    ("professeur", "teacher", ("ostedh", "ostedha", "oustedh", "ostedhi", "استاذ", "استاذة")),
    ("les cours", "the lessons", ("9raya", "qraya", "9rayya", "dars", "drous", "dourous", "قراية", "درس", "دروس")),
    ("de", "of", ("mte3", "mta3", "متاع")),
    ("mon / ma", "my", ("mte3i", "mta3i", "متاعي")),
    ("ce / cette", "this", ("hedhi", "hadhi", "hedha", "hadha", "hedhom", "هذي", "هذا")),
    ("chaque", "every", ("kol", "koll", "كل")),
    ("aussi", "also", ("zeda", "zada", "zeyda", "زادة")),
    ("mais", "but", ("ama", "amma", "اما")),
    ("parce que", "because", ("5ater", "khater", "3al khater", "خاطر")),
    ("si", "if", ("kan", "ken", "كان")),
    ("va (futur)", "will", ("bech", "bach", "باش")),
    ("assez", "enough", ("yezzi", "yezi", "يزي")),
    ("vrai", "true", ("s7i7", "sahi7", "sa7i7", "صحيح")),
    ("faux / erreur", "wrong / a mistake", ("ghalet", "8alet", "ghalta", "غالط", "غلطة")),
    ("problème", "problem", ("mochkla", "mouchkla", "machakel", "مشكلة")),
    ("l'heure", "the time", ("se3a", "sa3a", "ساعة")),
    ("numéro", "number", ("noumrou", "ra9m", "raqm", "نومرو")),
    ("bravo", "well done", ("sa7a", "sahha", "صحة")),
    ("mon ami", "my friend", ("sahbi", "sa7bi", "صاحبي")),
    ("rien", "nothing", ("chay", "chey", "حتى شي")),
    ("pas encore", "not yet", ("mazelt", "mazel", "مازال")),
    ("vite", "quickly", ("fissa3", "fisa3", "فيسع")),
    ("le prix", "the price", ("soum", "essoum", "السوم")),
    ("reste à payer", "left to pay", ("ba9i", "baki", "ba9ili", "باقي", "باقيلي")),
    ("payer", "to pay", ("نخلص", "نخلصو")),
    ("formation", "program", ("formasion", "فورماسيون", "التكوين", "تكوين")),
    ("cher", "expensive", ("ghali", "8ali", "غالي")),
    ("inscription", "registration", ("tasjil", "tasjila", "تسجيل")),
    # Doing things in the application (the guide and the assistant are asked “kifech nzid prof ?”).
    ("ajouter", "add", ("nzid", "zid", "nzidou", "tzid", "نزيد", "زيد", "أضيف", "اضيف", "إضافة", "اضافة")),
    ("changer / modifier", "change", ("nbadel", "badel", "nbaddel", "baddel", "tbadel", "نبدل", "بدل", "أغير", "اغير", "تغيير", "تعديل", "أعدل")),
    ("supprimer", "delete", ("nfasa5", "fasa5", "nfassakh", "fassakh", "tfasa5", "نفسخ", "فسخ", "أحذف", "احذف", "حذف")),
    ("voir", "see", ("nchouf", "chouf", "tchouf", "nchoof", "نشوف", "شوف", "أرى", "عرض")),
    ("ouvrir", "open", ("n7el", "7el", "t7el", "nhell", "نحل", "حل", "افتح", "أفتح")),
    ("écrire", "write", ("nekteb", "ekteb", "kteb", "tekteb", "نكتب", "اكتب", "أكتب")),
    ("rappeler", "remind", ("nfakker", "fakker", "fakkar", "tfakker", "نفكر", "فكر", "ذكر", "تذكير")),
    ("aide-moi", "help me", ("3aweni", "3awenni", "3awnni", "3awnouni", "عاوني", "عاونّي", "ساعدني")),
    ("j'ai besoin", "I need", ("nest7a9", "nesta7a9", "nest7a9ou", "نستحق", "أحتاج", "احتاج")),
    ("il manque / manquant", "missing", ("na9es", "na9sa", "nakes", "na9sin", "ناقص", "ناقصة", "ينقص", "نقص", "الناقص")),
    ("faire", "do", ("na3mel", "a3mel", "ta3mel", "n3amel", "نعمل", "اعمل", "تعمل")),
    ("trouver", "find", ("nal9a", "nalka", "l9it", "lgit", "نلقى", "لقيت")),
    ("je cherche", "I am looking for", ("nlawej", "nlawwej", "nfarrech", "نلوج", "أبحث", "ابحث")),
    ("possible", "possible", ("momken", "mumken", "ممكن", "يمكن")),
    ("se connecter", "sign in", ("nod5ol", "nodkhol", "nodkhel", "nod5el", "ندخل", "الدخول")),
    ("travail", "job", ("khedma", "5edma", "khidma", "خدمة")),
    ("stage", "internship", ("tarbos", "trabos", "تربص")),
    ("projet", "project", ("machrou3", "mechrou3", "مشروع")),
    ("photo", "photo", ("taswira", "tsawer", "تصويرة", "صورة")),
    ("vidéo", "video", ("vidyo", "فيديو")),
    ("traduction", "translation", ("tarjma", "tarjama", "ترجمة")),
    ("langue", "language", ("logha", "lougha", "لغة")),
    ("équipe", "team", ("ekip", "l'ekip", "فريق")),
    ("adresse", "address", ("3onwen", "onwen", "عنوان")),
    ("téléphone", "phone", ("tilifoun", "telifoun", "تليفون", "هاتف")),
    # Training
    ("classe", "class", ("9asm", "kasm", "el 9asm", "قسم", "الأقسام", "فصل")),
    ("séance", "session", ("7assa", "hassa", "7essa", "حصة", "الحصة", "حصص")),
    ("candidat / étudiant", "candidate", ("tilmidh", "tlamdha", "motarasha7", "talaba", "طالب", "تلميذ", "مترشح", "المترشحين")),
    ("le programme", "the program", ("barnamej", "el barnamej", "برنامج", "البرنامج")),
    ("certificat", "certificate", ("chahada", "chhada", "شهادة")),
    ("en ligne", "online", ("3an bo3d", "an bo3d", "عن بعد", "اونلاين")),
    ("en présentiel", "on site", ("7oudhouri", "houdhouri", "7odhouri", "حضوري")),
    ("niveau", "level", ("mostawa", "moustawa", "mestwa", "مستوى")),
    ("examen", "exam", ("imti7an", "imtihan", "امتحان", "اختبار")),
    ("message", "message", ("messaj", "mesaj", "risala", "رسالة", "مساج")),
    ("commence", "starts", ("yebda", "tebda", "nebda", "bdina", "يبدا", "تبدا", "نبدا", "يبدأ")),
    ("finir", "finish", ("youfa", "tekmel", "kammel", "ykammel", "يوفى", "كمل", "ينتهي")),
    ("horaire", "schedule", ("jadwel", "jadwal", "taw9it", "جدول", "التوقيت", "توقيت")),
    ("où est", "where is", ("winou", "winha", "winhom", "وينو", "وينها")),
    ("prochain", "next", ("jey", "jeya", "jeyin", "جاي", "جاية")),
    ("le paiement", "the payment", ("el khlas", "lkhlas", "el 5las", "الخلاص", "الدفع", "دفع")),
    ("réduction", "discount", ("takhfidh", "ta5fidh", "تخفيض")),
    ("par tranches", "in instalments", ("ta9sit", "taqsit", "taksit", "تقسيط")),
    ("gratuit", "free", ("blech", "bleech", "belech", "بلاش", "مجاني", "مجانا")),
    ("samedi", "Saturday", ("sebt", "essebt", "السبت")),
    ("dimanche", "Sunday", ("a7ad", "el a7ad", "الأحد", "الاحد")),
    ("lundi", "Monday", ("ethnin", "lethnin", "الاثنين")),
    # Arabic as it is written in the Middle East (questions, wishes)
    ("comment", "how", ("كيف", "كيفية")),
    ("quoi", "what", ("ماذا", "ماهو", "ماهي")),
    ("où", "where", ("أين", "اين")),
    ("quand", "when", ("متى",)),
    ("combien", "how much", ("كم", "بكم")),
    ("je veux", "I want", ("أريد", "اريد", "أود", "اود")),
    ("envoie", "send", ("أرسل", "ارسل", "إرسال", "ارسال")),
    ("le prix", "the price", ("سعر", "السعر", "الثمن", "ثمن")),
    ("l'inscription", "the registration", ("التسجيل", "أسجل", "اسجل")),
    ("merci", "thank you", ("شكرا", "شكراً")),
    ("formateur", "trainer", ("مكون", "المكون", "مدرب", "المدرب", "أستاذ")),
    ("semaine", "week", ("أسبوع", "اسبوع", "أسابيع")),
]

_ARABIC = re.compile(r"[؀-ۿ]")
_LETTERS = "ء-غـ-ي٠-٩ٱ-ۓ"          # Arabic letters and digits, not its punctuation (؟ ، ؛)
_SPLIT = re.compile(r"[^0-9A-Za-zÀ-ɏ" + _LETTERS + "']+")


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in text if not unicodedata.combining(char)).lower()


_INDEX: dict[str, tuple[str, str]] = {}
_PHRASES: list[tuple[str, tuple[str, str]]] = []
for french, english, spellings in _WORDS:
    for spelling in spellings:
        key = _plain(spelling)
        if " " in key:
            _PHRASES.append((key, (french, english)))
        else:
            _INDEX[key] = (french, english)


def _lookup(token: str) -> tuple[str, str] | None:
    """A word of the list, also with the Arabic article or a joined “and / with / for” in front (الحصص, والسعر, بالسعر)."""
    found = _INDEX.get(token)
    if found is None and _ARABIC.search(token):
        for prefix in ("وال", "بال", "فال", "كال", "لل", "ال", "و", "ب", "ل"):
            if token.startswith(prefix) and len(token) - len(prefix) >= 2:
                found = _INDEX.get(token[len(prefix):]) or _INDEX.get("ال" + token[len(prefix):])
                if found is not None:
                    break
    return found


# Read in French when they are there, but not counted as signs of Tunisian (“El” is in many names).
_WEAK = {"el", "l"}


def words_in(text: str) -> list[tuple[str, str, str]]:
    """The derja words of a text: [(as written, French, English)], in order, each once."""
    plain = _plain(text)
    found, seen = [], set()
    for phrase, meaning in _PHRASES:
        if re.search(r"(?<![0-9a-z؀-ۿ])" + re.escape(phrase) + r"(?![0-9a-z؀-ۿ])", plain) and phrase not in seen:
            seen.add(phrase)
            found.append((phrase, *meaning))
    for token in _SPLIT.split(plain):
        meaning = _lookup(token) if token else None
        if meaning is not None and token not in seen and token not in _WEAK:
            seen.add(token)
            found.append((token, *meaning))
    return found


def is_tunisian(text: str) -> bool:
    """Written (at least partly) in derja: two derja words, or Latin words with the numbers that stand for letters."""
    found = words_in(text)
    arabizi = re.search(r"(?<![0-9])[a-z]*[3795][a-z]+", _plain(text)) is not None
    return len(found) >= 2 or (len(found) >= 1 and arabizi)


def has_arabic_letters(text: str) -> bool:
    return bool(_ARABIC.search(text or ""))


def hints(text: str, language: str = "French") -> str:
    """For the model: the derja words of the text and what they mean, in the language it writes."""
    found = words_in(text)
    if not found:
        return ""
    english = language.lower().startswith("en")
    pairs = "; ".join(f"“{word}” = {meaning_en if english else meaning_fr}" for word, meaning_fr, meaning_en in found[:30])
    return ("The note is partly in Tunisian Arabic (derja: Latin letters where 3 = ع, 7 = ح, 9 = ق, 5 = خ, 8 = غ, or Arabic "
            f"letters). Its derja words mean: {pairs}.")


def to_french(text: str) -> str:
    """The text with its derja words replaced by their French meaning: what the rules and the search can read.
    Latin and Arabic letters; the rest of the text (French, English, names, numbers) stays as written."""
    result = text or ""
    for phrase, (french, _) in sorted(_PHRASES, key=lambda item: -len(item[0])):
        result = re.sub(r"(?i)(?<![0-9A-Za-z])" + re.escape(phrase) + r"(?![0-9A-Za-z])", french, result)

    def swap(match):
        word = match.group(0)
        meaning = _lookup(_plain(word))
        return meaning[0] if meaning else word

    return re.sub(r"[0-9A-Za-zÀ-ɏ" + _LETTERS + "']+", swap, result)


def arabic_coverage(text: str) -> float:
    """The share of the words in Arabic letters that the list knows (how much of the message can be read)."""
    words = re.findall("[" + _LETTERS + "]+", text or "")
    if not words:
        return 0.0
    return sum(1 for word in words if _lookup(_plain(word)) is not None) / len(words)


def is_derja_word(word: str) -> bool:
    plain = _plain(word)
    return plain in _INDEX or has_arabic_letters(word)
