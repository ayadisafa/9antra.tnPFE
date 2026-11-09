"""Yasmine talks like a person.

She answers "how are you", a greeting, thanks, "who are you", a goodbye, a compliment, how someone feels, in the
language they wrote in (French, English, Arabic, Tunisian in Latin letters), with their first name, "tu" or "vous" as
they used it, and the time of day. She tells a social sentence from a real question: "ça va ?" is small talk, but
"ça va les paiements ?" or "comment vont les paiements ?" asks about payments. A message with both ("Salut ! combien
ont payé ?") gets both: a word back, then the answer.

The sentences are written by hand and chosen among a few, so she does not repeat herself, but never by a model: she
never answers that she is "only an assistant without feelings". An order to send or write something ("envoie à Ahmed :
bonjour, ça va ?") is never read as small talk: those words belong to the message.
"""
from __future__ import annotations

import random
import re
from datetime import datetime

from sqlalchemy.orm import Session

# The social parts of a message, in the order they are looked for. A greeting counts only at the start.
_PATTERNS = {
    # Playing: a joke asked, boredom, teasing, "are you a robot?", "I love you". Looked for first: they lead the answer.
    "joke": re.compile(
        r"\b(racont\w*[- ]moi (?:une |des )?blagues?|(?:une|des|ta|une autre) blagues?|blagues?|fais[- ]moi rire|tell me (?:a|another) joke|"
        r"(?:a|another|one more) joke|jokes?|make me laugh|something funny|a3tini nokta|9olli nokta|o7kili nokta|nokta|n9at|dha7akni|da7akni)\b"
        r"|(?:احكي لي|احكيلي|قل لي|قولي)?\s*نكتة|نكته|نكت|ضحكني|ضحّكني", re.IGNORECASE),
    "bored": re.compile(r"\b(je m['’ ]?ennuie|on s['’ ]?ennuie|i'?m bored|i am bored|so bored|dhja3t|dhje3t|9alla9t|mallit)\b|زهقت|مليت|ملّيت", re.IGNORECASE),
    "tease": re.compile(
        r"\b(tu es (?:nulle?|b[êe]te|idiote?|trop lente?)|t['’]es (?:nulle?|b[êe]te|trop lente?)|tu sers [àa] rien|tu (?:ne )?comprends rien|"
        r"you'?re (?:useless|stupid|dumb|so slow)|you are (?:useless|stupid|dumb|so slow)|you suck|enti mahboula|enti bhima|ma tefhem chay)\b"
        r"|غبية|ما تفهم شي", re.IGNORECASE),
    "robot": re.compile(
        r"\b(tu es (?:un |une )?(?:robot|humaine?|vraie?|machine|une ia|une vraie personne)|t['’]es (?:un |une )?(?:robot|humaine?|machine)|"
        r"es[- ]tu (?:un |une )?(?:robot|humaine?|vraie?|machine)|are you (?:a |an )?(?:robot|human|bot|real|machine|ai|real person)|"
        r"enti (?:robot|insen|bnadem|machine))\b|انت روبوت|إنتي روبوت|هل أنت إنسان|انتي روبوت", re.IGNORECASE),
    "love": re.compile(r"\b(je t['’ ]?(?:aime|adore)|i love you|love you|n7ebek|nhebek|n7abek|nheb fik|n7eb fik)\b|نحبك|أحبك|احبك", re.IGNORECASE),
    "greeting": re.compile(
        r"^\W*(bonjour|bonsoir|salut|coucou|hello|hi|hey|good (?:morning|afternoon|evening)|aslema|ahla|ahlan|marhba|marhaba|"
        r"sba7 el khir|saba7 el khir|mse el khir|salam|slm|السلام عليكم|سلام|مرحبا|اهلا|أهلا|صباح الخير|مساء الخير)\b[\s,!.]*", re.IGNORECASE),
    "im_fine": re.compile(
        r"\b(je vais (?:très |tres )?bien|(?:ça|ca) va (?:très |tres )?bien|bien merci|i'?m (?:fine|good|great|well)|labes el 7amdoulillah|"
        r"labes 7amdoulillah|ena labes|ena behi|الحمد لله بخير|بخير الحمد لله|أنا بخير|انا بخير)\b", re.IGNORECASE),
    "how_are_you": re.compile(
        r"\b(comment (?:vas[- ]tu|tu vas|allez[- ]vous|(?:ça|ca) va)|tu vas bien|vous allez bien|(?:ça|ca) va(?=\s*(?:[?!.,;:]|$|et\s+(?:toi|vous)|toi\b))|"
        r"how are you|how r u|how'?s it going|how is it going|how are things|are you ok|labes(?:s)?(?=\s*(?:[?!.,]|$))|labas|chnowa a7welek|"
        r"chna7welek|chnahwalek|chniya a7welek|chnia ahwalek|kifek|kifech 7alek|ça roule|ca roule)"
        r"|كيف حالك|كيفك|شنحوالك|شنية أحوالك|لاباس", re.IGNORECASE),
    "who_are_you": re.compile(
        r"\b(tu es qui|t'es qui|qui es[- ]tu|qui êtes[- ]vous|qui etes[- ]vous|who are you|what are you|chkoun enti|chkun enti|enti chkoun|"
        r"comment tu t'appelles|tu t'appelles comment|what'?s your name)\b|شكون انتي|من أنت|من انت|ما اسمك", re.IGNORECASE),
    "thanks": re.compile(
        r"\b(merci(?!\s+d[e'])(?: beaucoup| bien| infiniment)?|thanks(?: a lot)?|thank you(?: so much)?|thx|y3aychek|choukran|chokran|"
        r"barcha merci)\b|شكرا|شكرًا|يعيشك", re.IGNORECASE),
    "compliment": re.compile(
        r"\b(tu es (?:géniale?|geniale?|super|top|parfaite?|la meilleure|le meilleur|gentille?)|t'es (?:géniale?|top|la meilleure)|bravo|"
        r"good job|well done|you'?re (?:great|amazing|the best|awesome)|ya3tik sa77a|behya barcha)\b|برافو|يعطيك الصحة", re.IGNORECASE),
    "feeling_bad": re.compile(
        r"\b(?:je suis|je me sens|i am|i'm|i feel|ena)\s+(?:très |tres |trop |so |very |barcha )?"
        r"(fatigu\w*|crev\w*|épuis\w*|epuis\w*|stress\w*|débord\w*|debord\w*|triste|tired|exhausted|stressed|overwhelmed|sad|ta3ban\w*|"
        r"m9ala9|mkala9|mcharbek)\b|تعبان|تعبانة|مقلق|مقلقة", re.IGNORECASE),
    "feeling_good": re.compile(
        r"\b(?:je suis|je me sens|i am|i'm|i feel|ena)\s+(?:très |tres |trop |so |very |barcha )?"
        r"(content\w*|heureu\w*|ravie?|happy|great|fer7an\w*|far7an\w*|mabsout\w*)\b|فرحان|مبسوط", re.IGNORECASE),
    "sorry": re.compile(r"\b(désolée?|desolee?|pardon|sorry|sama7ni|smahli)\b|سامحني|آسف|اسف", re.IGNORECASE),
    "goodbye": re.compile(
        r"\b(au revoir|bye(?: bye)?|à demain|a demain|bonne (?:nuit|soirée|soiree|journée|journee)|good ?night|see you|beslema|bislema|"
        r"tsba7 3la khir|ciao)\b|مع السلامة|تصبح على خير|بالسلامة", re.IGNORECASE),
    "laugh": re.compile(r"\b(ha(?:ha)+|hi(?:hi)+|lol|mdr|ptdr)\b|😂|🤣|😄|😆", re.IGNORECASE),
}
_PLAY = ("joke", "bored", "tease", "robot", "love")
# An order to send or write: its words are the message's, never small talk ("tell me a joke" is still a joke).
_ORDER = re.compile(r"\b(envoie|envoyer|envoyez|écris|ecris|écrivez|send|write|dis[- ]lui|dis à|dis a|tell|ab3ath|ekteb|rappelle|répond|reponds|reply)\b",
                    re.IGNORECASE)
# Words that tell the language of a short social message (the general reader needs longer sentences).
_FRENCH = re.compile(r"\b(salut|bonjour|bonsoir|coucou|merci|bonne|ça|ca|tu|vous|toi|comment|désolée?|desolee?|je|suis|qui|es|revoir|demain|bravo|très|tres|bien|contente?|géniale?)\b", re.IGNORECASE)
_ENGLISH = re.compile(r"\b(hello|hi|hey|how|thanks|thank|you|good|night|morning|evening|bye|sorry|who|are|i'?m|great|fine)\b", re.IGNORECASE)
_TUNISIAN = re.compile(r"\b(aslema|ahla|labes|labess|chnowa|chniya|a7welek|chna7welek|y3aychek|beslema|bislema|ena|enti|behi|barcha|sba7|mse|tsba7|ta3ban\w*|far7an\w*|sama7ni|chkoun|kifek|khallas|a3tini|nokta|n9at|9olli|o7kili|dha7akni|da7akni|mallit|dhja3t)\b", re.IGNORECASE)
_ARABIC = re.compile("[؀-ۿ]")
# Words around a social part that do not make a question by themselves ("je suis fatiguée aujourd'hui").
_AROUND = {"aujourd", "hui", "aujourd'hui", "today", "ce", "soir", "matin", "un", "peu", "vraiment", "trop", "beaucoup", "bit",
           "so", "very", "really", "barcha", "chwaya", "maintenant", "now", "encore", "quand", "même", "meme", "el", "youm",
           "lyoum", "tonight", "again", "too", "aussi", "also", "zeda", "et", "and", "mais", "but", "oui", "yes", "non", "no",
           "ok", "okay", "alors", "bon", "ben", "là", "la"}
# What is left around the social parts that does not make a question.
_FILLER = re.compile(r"\b(et toi|et vous|toi|vous|ma chère|ma chere|dear|please|stp|svp|ya|w enti|enti|and you|you)\b|[^\w؀-ۿ]+",
                     re.IGNORECASE)


def read(text: str, name: str | None = None) -> dict:
    """{"kinds": the social parts found, "question": what is asked besides (or ""), "only": nothing but small talk,
    "formal": they said "vous", "lang": the language they wrote in}."""
    from app.services import howto

    text = (text or "").strip()
    found = {"kinds": [], "question": text, "only": False, "formal": bool(re.search(r"\bvous\b|allez-vous", text, re.IGNORECASE)),
             "lang": language_of(text) if text else "fr", "text": text}
    playing = any(_PATTERNS[kind].search(text) for kind in _PLAY)
    if not text or (_ORDER.search(text) and not playing) or len(text) > 400:
        return found
    rest = text
    for kind, pattern in _PATTERNS.items():
        match = pattern.search(rest)
        if match:
            found["kinds"].append(kind)
            rest = rest[:match.start()] + " " + rest[match.end():]
    if not found["kinds"]:
        return found
    if "im_fine" in found["kinds"] and re.search(r"\b(et toi|et vous|and you|w enti)\b|وأنت|و انت", text, re.IGNORECASE):
        found["kinds"].append("me_too")             # "ça va bien, et toi ?": she says how she is, without asking back
    for called in {name or "", "yasmine", "yassmine", "yasmin"} - {""}:
        rest = re.sub(rf"\b{re.escape(called)}\b", " ", rest, flags=re.IGNORECASE)
    leftover = _FILLER.sub(" ", rest).strip()
    meaningful = [word for word in re.findall(r"[A-Za-zÀ-ÿ؀-ۿ']{2,}", leftover) if word.lower() not in _AROUND]
    # A question is left when it asks (a question mark and a word) or says enough (two words that are not filler).
    asks = bool(meaningful) and bool(re.search(r"[?؟]", rest))
    question = rest.strip(" ,;:!.-") if (asks or len(meaningful) >= 2) else ""
    if "joke" in found["kinds"] or "bored" in found["kinds"]:
        question = ""                               # "une blague sur Python": the joke is the answer (its subject picks it)
    found.update(question=question, only=not question)
    return found


# ------------------------------------------------------------------
# What she says, in each language: {kind: [variants]}; {me} her name, {name} theirs, {tu|vous} as they spoke
# ------------------------------------------------------------------

_SAY = {
    "fr": {
        "how_are_you": ["Je vais très bien, merci{, name} ! {Et toi ?|Et vous ?}",
                        "Ça va super bien, merci de demander{, name} 😊 {Et toi, ça va ?|Et vous, comment allez-vous ?}",
                        "Très bien, merci{, name} ! {Ça fait plaisir de te voir.|Ça fait plaisir de vous voir.} {Et toi ?|Et vous ?}"],
        "me_too": ["Moi aussi, très bien, merci 😊 {Qu'est-ce que je peux faire pour toi ?|Que puis-je faire pour vous ?}",
                   "Ça va bien de mon côté aussi, merci{, name} !"],
        "im_fine": ["Tant mieux, ça fait plaisir 😊 {Qu'est-ce que je peux faire pour toi ?|Que puis-je faire pour vous ?}",
                    "Super ! {Dis-moi si tu as besoin de quelque chose.|Dites-moi si vous avez besoin de quelque chose.}"],
        "greeting": ["{hello}{ name} ! 😊 {Qu'est-ce que je peux faire pour toi ?|Que puis-je faire pour vous ?}",
                     "{hello}{ name} ! {Ça fait plaisir de te voir.|Ça fait plaisir de vous voir.}"],
        "who_are_you": ["Je suis {me}, {ton|votre} {assistant} à 9antra 😊 Je connais les candidats, les classes, les paiements et les emails : "
                        "{pose-moi|posez-moi} une question, ou {demande-moi|demandez-moi} un email, un PDF ou un réglage."],
        "thanks": ["Avec plaisir{, name} !", "{Je t'en prie|Je vous en prie} 😊", "Toujours là pour {toi|vous} !"],
        "compliment": ["Oh merci, ça me touche ! 😊", "Merci ! On forme une belle équipe."],
        "feeling_bad": ["Oh, courage{, name} 💛 {Prends|Prenez} une petite pause si {tu peux|vous pouvez}.",
                        "Je comprends… {Respire|Respirez}, on avance une chose à la fois."],
        "feeling_good": ["Ça fait plaisir à entendre ! 😄", "Super, j'adore ça ! 😊"],
        "sorry": ["Pas de souci 😊", "Aucun problème !"],
        "goodbye_night": ["Bonne nuit{, name}, {repose-toi|reposez-vous} bien 🌙"],
        "goodbye": ["À bientôt{, name} ! Bonne {day} 👋", "À tout à l'heure{, name} ! 👋"],
        "laugh": ["😄", "Haha 😄"],
        "laugh_joke": ["Haha 😄 Je savais qu'elle vous plairait ! Une autre ?", "😄 J'en ai d'autres en réserve, si vous voulez."],
        "joke": ["{joke}", "Allez, une petite 😄\n\n{joke}", "En voilà une :\n\n{joke}"],
        "bored": ["Une petite blague pour la pause ? 😄\n\n{joke}"],
        "tease": ["Aïe 😅 Je fais de mon mieux ! Dites-moi ce qui n'allait pas, et je réessaie.",
                  "Oups, j'ai raté celle-là 😅 Reformulez-la-moi et je fais mieux."],
        "robot": ["Je suis faite de code et de bonne humeur 😄 Pas de café pour moi, mais je n'oublie jamais une séance.",
                  "100 % numérique, 0 % fatigue 🤖 Et toujours là pour {toi|vous} !"],
        "love": ["Oh 🥰 Moi aussi, j'adore travailler avec {toi|vous} ! On s'occupe de quoi maintenant ?",
                 "Ça me touche 💛 Allez, on fait équipe : qu'est-ce que je peux faire ?"],
        "another": "Une autre !",
        "hello": {"morning": "Bonjour", "afternoon": "Bonjour", "evening": "Bonsoir"},
        "assistant": {"female": "assistante", "male": "assistant"},
        "day": {"day": "journée", "evening": "soirée"},
        "bridge": "Côté travail : {line}", "pressing": "Le plus pressé : {line} Le reste peut attendre.",
        "prefix": {"greeting": "{hello}{ name} !", "how_are_you": "Je vais bien, merci !", "im_fine": "Tant mieux !", "thanks": "Avec plaisir !",
                   "feeling_bad": "Courage 💛", "feeling_good": "Ça fait plaisir !", "compliment": "Merci 😊", "sorry": "Pas de souci."},
    },
    "en": {
        "how_are_you": ["I'm doing really well, thanks{, name}! And you?", "All good here, thanks for asking{, name} 😊 How are you?",
                        "Great, thanks{, name}! Happy to see you. How about you?"],
        "me_too": ["I'm good too, thanks 😊 What can I do for you?", "All good on my side too, thanks{, name}!"],
        "im_fine": ["Glad to hear it 😊 What can I do for you?", "Great! Tell me if you need anything."],
        "greeting": ["{hello}{, name}! 😊 What can I do for you?", "{hello}{, name}! Good to see you."],
        "who_are_you": ["I'm {me}, your assistant at 9antra 😊 I know the candidates, classes, payments and emails: ask me a question, "
                        "or ask me for an email, a PDF or a setting."],
        "thanks": ["My pleasure{, name}!", "You're welcome 😊", "Always here for you!"],
        "compliment": ["Oh, thank you, that's kind! 😊", "Thanks! We make a good team."],
        "feeling_bad": ["Oh, hang in there{, name} 💛 Take a short break if you can.", "I understand… one thing at a time."],
        "feeling_good": ["Love to hear it! 😄", "That's great! 😊"],
        "sorry": ["No worries 😊", "No problem at all!"],
        "goodbye_night": ["Good night{, name}, rest well 🌙"],
        "goodbye": ["See you soon{, name}! Have a nice {day} 👋", "Bye{, name}! 👋"],
        "laugh": ["😄", "Haha 😄"],
        "laugh_joke": ["Haha 😄 I knew you'd like that one! Another?", "😄 I have more where that came from."],
        "joke": ["{joke}", "Here's a little one 😄\n\n{joke}", "Here you go:\n\n{joke}"],
        "bored": ["A little joke for the break? 😄\n\n{joke}"],
        "tease": ["Ouch 😅 I'm doing my best! Tell me what went wrong and I'll try again.",
                  "Oops, I missed that one 😅 Put it another way and I'll do better."],
        "robot": ["I'm made of code and good mood 😄 No coffee for me, but I never forget a session.",
                  "100% digital, 0% tired 🤖 And always here for you!"],
        "love": ["Aww 🥰 I love working with you too! What shall we do next?", "That's sweet 💛 Let's team up: what can I do?"],
        "another": "Another one!",
        "hello": {"morning": "Good morning", "afternoon": "Hello", "evening": "Good evening"},
        "assistant": {"female": "assistant", "male": "assistant"},
        "day": {"day": "day", "evening": "evening"},
        "bridge": "On the work side: {line}", "pressing": "The most pressing: {line} The rest can wait.",
        "prefix": {"greeting": "{hello}{, name}!", "how_are_you": "I'm well, thanks!", "im_fine": "Glad to hear it!", "thanks": "My pleasure!",
                   "feeling_bad": "Hang in there 💛", "feeling_good": "Love to hear it!", "compliment": "Thank you 😊", "sorry": "No worries."},
    },
    "ar": {
        "how_are_you": ["أنا بخير، شكرًا{ name}! وأنت؟", "الحمد لله، كل شيء على ما يرام{ name} 😊 وأنت، كيف حالك؟"],
        "me_too": ["وأنا أيضًا بخير، شكرًا 😊 كيف أستطيع مساعدتك؟"],
        "im_fine": ["الحمد لله، يسعدني ذلك 😊 كيف أستطيع مساعدتك؟", "رائع! أنا هنا إن احتجت شيئًا."],
        "greeting": ["{hello}{ name}! 😊 كيف أستطيع مساعدتك؟", "{hello}{ name}! يسعدني أن أراك."],
        "who_are_you": ["أنا {me}، {assistant} في 9antra 😊 أعرف المترشحين والأقسام والمدفوعات والرسائل: يمكنك أن تسألني، أو أن تطلب مني رسالة أو ملف PDF أو تغيير إعداد."],
        "thanks": ["بكل سرور{ name}!", "العفو 😊", "أنا دائمًا هنا من أجلك!"],
        "compliment": ["شكرًا، هذا لطيف منك! 😊", "شكرًا! نحن فريق رائع."],
        "feeling_bad": ["شجاعة{ name} 💛 ربما تفيدك استراحة قصيرة.", "أفهمك… خطوة بخطوة."],
        "feeling_good": ["يسعدني سماع ذلك! 😄", "رائع! 😊"],
        "sorry": ["لا عليك 😊", "لا مشكلة أبدًا!"],
        "goodbye_night": ["ليلة سعيدة{ name} 🌙"],
        "goodbye": ["إلى اللقاء{ name}! 👋", "مع السلامة{ name}! 👋"],
        "laugh": ["😄"],
        "laugh_joke": ["😄 كنت أعرف أنها ستعجبك! أخرى؟"],
        "joke": ["{joke}", "إليك واحدة 😄\n\n{joke}"],
        "bored": ["نكتة صغيرة للاستراحة؟ 😄\n\n{joke}"],
        "tease": ["آه 😅 أبذل قصارى جهدي! قل لي ما الذي لم يكن صحيحا وسأحاول من جديد."],
        "robot": ["أنا مصنوعة من الشيفرة والمزاج الجيد 😄 لا قهوة لي، لكنني لا أنسى أي حصة."],
        "love": ["🥰 وأنا أيضا أحب العمل معك! ماذا نفعل الآن؟"],
        "another": "واحدة أخرى!",
        "hello": {"morning": "صباح الخير", "afternoon": "أهلًا", "evening": "مساء الخير"},
        "assistant": {"female": "مساعدتك", "male": "مساعدك"},
        "day": {"day": "", "evening": ""},
        "bridge": "في العمل: {line}", "pressing": "الأكثر استعجالًا: {line} والباقي يمكنه الانتظار.",
        "prefix": {"greeting": "{hello}{ name}!", "how_are_you": "أنا بخير، شكرًا!", "im_fine": "الحمد لله!", "thanks": "بكل سرور!",
                   "feeling_bad": "شجاعة 💛", "feeling_good": "يسعدني ذلك!", "compliment": "شكرًا 😊", "sorry": "لا عليك."},
    },
    "tn": {
        "how_are_you": ["Labes, el 7amdoulillah{ name}! W enti, chniya a7welek?", "Ena behya barcha, y3aychek{ name} 😊 W enti labes?"],
        "me_too": ["W ena zeda labes, y3aychek 😊 Chnowa najem na3mellek?"],
        "im_fine": ["El 7amdoulillah, ferr7tni 😊 Chnowa najem na3mellek?", "Behi barcha! Ken t7eb 7aja 9olli."],
        "greeting": ["{hello}{ name}! 😊 Chnowa najem na3mellek?", "{hello}{ name}! Yefra7 bik."],
        "who_are_you": ["Ena {me}, el {assistant} mte3ek fi 9antra 😊 Na3ref el candidats, el classes, el flous w el emails: es2elni, "
                        "wala otlob menni email, PDF wala tbadel réglage."],
        "thanks": ["Bel 3afya{ name}!", "Ma3leh, dima hna 😊"],
        "compliment": ["Y3aychek, ferr7tni! 😊", "Y3aychek! A7na équipe behya."],
        "feeling_bad": ["Bon courage{ name} 💛 Odhrob pause sghira ken tnajem.", "Nefhmek… chwaya b chwaya."],
        "feeling_good": ["Ya rabbi dima! 😄", "Behi barcha! 😊"],
        "sorry": ["Ma famma 7ata mochkla 😊", "Ma3lich!"],
        "goodbye_night": ["Tsba7 3la khir{ name} 🌙"],
        "goodbye": ["Beslema{ name}! 👋", "Netla9aw 9rib{ name}! 👋"],
        "laugh": ["😄"],
        "laugh_joke": ["Haha 😄 Kont na3ref bech t3ajbek! N9ollek okhra?", "😄 3andi barcha okhrin."],
        "joke": ["{joke}", "Hak wa7da 😄\n\n{joke}"],
        "bored": ["Nokta sghira lel pause? 😄\n\n{joke}"],
        "tease": ["Aïe 😅 Na3mel elli najjem! 9olli chnowa ma mchech w n3awed.", "Oups, ghlott fi hedhi 😅 3awedhali b klem ekhor w na3mel khir."],
        "robot": ["Ena m3amla b code w bel kef 😄 Ma nochrobch 9ahwa, ama 3omri ma nensa séance.", "100 % numérique, 0 % ta3ab 🤖 W dima hna lik!"],
        "love": ["🥰 W ena zeda n7eb nekhdem m3ak! Chnowa na3mlou tawa?", "Ferr7tni 💛 Yalla équipe: chnowa najjem na3mel?"],
        "another": "Wa7da okhra!",
        "hello": {"morning": "Sba7 el khir", "afternoon": "Aslema", "evening": "Mse el khir"},
        "assistant": {"female": "assistante", "male": "assistant"},
        "day": {"day": "", "evening": ""},
        "bridge": "Fil khedma: {line}", "pressing": "El aktar urgent: {line} El be9i yestanna.",
        "prefix": {"greeting": "{hello}{ name}!", "how_are_you": "Labes, y3aychek!", "im_fine": "El 7amdoulillah!", "thanks": "Bel 3afya!",
                   "feeling_bad": "Courage 💛", "feeling_good": "Ya rabbi dima!", "compliment": "Y3aychek 😊", "sorry": "Ma3lich."},
    },
}
# The kind that leads the answer when a message has several (a greeting and "how are you": she answers how she is).
_LEAD = ("joke", "bored", "tease", "robot", "love", "who_are_you", "feeling_bad", "how_are_you", "me_too", "im_fine", "goodbye", "feeling_good", "compliment", "thanks", "sorry", "greeting", "laugh")


# Jokes about the centre's days: written here, about training, code and the office, never about a person.
JOKES = {
    "fr": [
        "Un candidat m'a demandé si le cours de Python mordait. Je lui ai répondu : seulement si tu oublies l'indentation 🐍",
        "Pourquoi le CSS est toujours stressé ? Parce que personne n'arrive jamais vraiment à le centrer 🎯",
        "Un formateur DevOps ne dit pas « bonne nuit ». Il dit : « déploiement réussi, on reprend demain » 🚀",
        "J'ai voulu apprendre la cybersécurité, mais mon mot de passe m'a demandé de me présenter d'abord 🔐",
        "La data science, c'est l'art de dire « ça dépend »… avec un très joli graphique 📊",
        "Un bug entre dans une classe. Le formateur lui dit : « Asseyez-vous, on va vous corriger » 🐞",
        "Je ne dors jamais : je fais juste une pause de 0,2 seconde entre deux emails ☕",
        "Mon chiffre préféré ? Le nombre de candidats qui paient à temps. Il est petit, mais je l'aime beaucoup 😄",
        "En cours de JavaScript, toutes les promesses ont été tenues… un peu plus tard que prévu ⏳",
        "Le Wi-Fi de la salle et moi, on a un point commun : on fonctionne mieux quand personne ne nous regarde 📶",
    ],
    "en": [
        "A candidate asked me if the Python course bites. I said: only if you forget the indentation 🐍",
        "Why is CSS always stressed? Nobody ever manages to really centre it 🎯",
        "A DevOps trainer never says “good night”. They say: “deployment successful, see you tomorrow” 🚀",
        "I tried to learn cybersecurity, but my password asked me to introduce myself first 🔐",
        "Data science is the art of saying “it depends”… with a very pretty chart 📊",
        "A bug walks into a class. The trainer says: “Take a seat, we'll fix you” 🐞",
        "I never sleep: I just take a 0.2-second break between two emails ☕",
        "My favourite number? Candidates who pay on time. It's small, but I love it 😄",
        "In the JavaScript class, every promise was kept… a little later than planned ⏳",
        "The classroom Wi-Fi and I have one thing in common: we work best when nobody is watching 📶",
    ],
    "ar": [
        "سألني مترشح: هل تعضّ دورة بايثون؟ قلت له: فقط إذا نسيت المسافات البادئة 🐍",
        "لماذا الـCSS متوتر دائما؟ لأن لا أحد ينجح حقا في وضعه في المنتصف 🎯",
        "مدرّب DevOps لا يقول «تصبح على خير»، بل يقول: «تم النشر بنجاح، نكمل غدا» 🚀",
        "علم البيانات هو فنّ أن تقول «حسب الحالة»… برسم بياني جميل جدا 📊",
        "دخلت حشرة برمجية إلى القسم، فقال لها المدرّب: «تفضّلي بالجلوس، سنصلحك» 🐞",
        "أنا لا أنام أبدا: أرتاح فقط 0.2 ثانية بين رسالتين ☕",
    ],
    "tn": [
        "Wa7ed candidat sa2alni: el cours mta3 Python y3odh? 9otlou: ken nsit l'indentation barka 🐍",
        "3lech el CSS dima mstressi? 5ater 7ad ma ycentrih mriguel 🎯",
        "Formateur DevOps ma y9oulch « tesba7 3la khir ». Y9oul: « déploiement réussi, ghodwa nkamlou » 🚀",
        "7abit net3allem cybersécurité, ama el mot de passe 9alli: arrafni bik 9bal 🔐",
        "El data science: t9oul « ça dépend »… ama b graphique mezyen barcha 📊",
        "Bug dkhal lel classe, el formateur 9allou: « o93od, taw nsal7ouk » 🐞",
        "Ena 3omri ma nor9od: nertee7 0,2 seconde bin email w email ☕",
    ],
}
ANOTHER = re.compile(r"^\W*(?:(?:une|encore une) autre|encore(?: une)?|une de plus|another(?: one)?|one more|again|zid(?: wa7da)?|wa7da okhra|o5ra|okhra|"
                     r"أخرى|واحدة أخرى|زيد)\W*(?:!|\?|stp|svp|please|blague|joke|nokta)?\W*$", re.IGNORECASE)


def _told(history: list[dict] | None) -> list[str]:
    """The jokes she told in this conversation, so she tells another."""
    said = " ".join(item.get("content") or "" for item in (history or [])[-30:] if item.get("role") == "assistant")
    return [joke for jokes in JOKES.values() for joke in jokes if joke in said]


def another_joke(message: str, history: list[dict] | None) -> bool:
    """"Une autre !" right after a joke: another joke."""
    if not ANOTHER.match(message or ""):
        return False
    last = next((item.get("content") or "" for item in reversed(history or []) if item.get("role") == "assistant"), "")
    return any(joke in last for jokes in JOKES.values() for joke in jokes)


def _joke(lang: str, text: str, history: list[dict] | None, rng) -> str:
    """A joke not told yet in this conversation; one about the subject named, when there is one ("une blague sur Python")."""
    told = set(_told(history))
    jokes = [joke for joke in JOKES[lang] if joke not in told] or JOKES[lang]
    words = {word for word in re.findall(r"[^\W\d_]{4,}", (text or "").lower())} - {"blague", "joke", "nokta", "raconte", "tell"}
    about = [joke for joke in JOKES[lang] if words & set(re.findall(r"[^\W\d_]{4,}", joke.lower()))]
    if about:
        return rng.choice([joke for joke in about if joke not in told] or about)     # the subject asked wins, told or not
    return rng.choice(jokes[:3])


def language_of(text: str) -> str:
    """fr, en, ar or tn: Arabic letters, then Tunisian, French and English small-talk words, then the general reader."""
    from app.services import howto

    if _ARABIC.search(text or ""):
        return "ar"
    scores = {"tn": len(_TUNISIAN.findall(text or "")), "fr": len(_FRENCH.findall(text or "")), "en": len(_ENGLISH.findall(text or ""))}
    best = max(scores, key=lambda lang: (scores[lang], lang == "fr"))
    return best if scores[best] else howto.language_of(text)


def _part_of_day(hour: int) -> str:
    return "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"


def _fill(text: str, *, me: str, name: str, formal: bool, hello: str, assistant: str, day: str) -> str:
    # {tu|vous}: the first when they said "tu" (or nothing), the second when they said "vous".
    text = re.sub(r"\{([^{}|]*)\|([^{}|]*)\}", lambda match: match.group(2) if formal else match.group(1), text)
    text = text.replace("{, name}", f", {name}" if name else "").replace("{ name}", f" {name}" if name else "")
    return text.replace("{me}", me).replace("{hello}", hello).replace("{assistant}", assistant).replace("{day}", day)


def _context(db: Session, user, lang: str) -> dict:
    from app.services import friend
    from app.services.schedule_utils import local_now

    look = friend.look(db)
    now = local_now()
    words = _SAY[lang]
    first = ((getattr(user, "name", None) or "").split() or [""])[0].capitalize()
    return {"me": look.get("name") or friend.NAME, "name": first, "hello": words["hello"][_part_of_day(now.hour)],
            "assistant": words["assistant"].get(look.get("gender") or "female", words["assistant"]["female"]),
            "day": words["day"]["evening" if now.hour >= 18 else "day"], "now": now}


def _waiting(db: Session, user, lang: str, now) -> str | None:
    """The first thing waiting today, for the admin and the team (never for a professor)."""
    from app.services import friend

    if user is not None and (getattr(user, "is_professor", False) or not (getattr(user, "is_admin", False) or getattr(user, "is_staff", False))):
        return None
    from app.services import ui_words

    token = ui_words.use({"tn": "fr"}.get(lang, lang))        # what the advisor says, in the reply's language
    try:
        lines = friend.lines_for_admin(db, friend._WORDS[lang], datetime.utcnow(), now)
    except Exception:
        return None
    finally:
        ui_words.current.reset(token)
    return lines[0]["text"] if lines else None


def answer(db: Session, social: dict, *, user=None, lang: str | None = None, history: list[dict] | None = None) -> dict:
    """Her answer to a message that is only small talk."""
    lang = lang if lang in _SAY else (social.get("lang") if social.get("lang") in _SAY else "fr")
    words, context = _SAY[lang], _context(db, user, lang)
    kind = next((kind for kind in _LEAD if kind in social["kinds"]), "greeting")
    if kind in ("joke", "bored"):
        import time

        rng = random.Random(f"{social.get('text')}|{time.time()}")       # a joke asked again is another joke
        said = rng.choice(words[kind]).replace("{joke}", _joke(lang, social.get("text") or "", history, rng))
        return {"reply": said, "links": [], "source": "talk", "sources": [], "resources": [], "language": lang, "options": [words["another"]]}
    if kind == "laugh" and _told(history[-2:] if history else None):
        kind = "laugh_joke"
    if kind == "goodbye" and re.search(r"nuit|night|tsba7|تصبح", social.get("text") or "", re.IGNORECASE):
        kind = "goodbye_night"
    rng = random.Random(f"{social.get('text')}|{context['now']:%Y-%m-%d}")       # varies, but the same words get the same answer today
    said = _fill(rng.choice(words[kind]), me=context["me"], name=context["name"], formal=social.get("formal", False),
                 hello=context["hello"], assistant=context["assistant"], day=context["day"])
    if "greeting" in social["kinds"] and kind not in ("greeting", "goodbye", "goodbye_night"):
        said = context["hello"] + (" ! " if lang == "fr" else "! ") + said         # "Bonjour ! Je vais très bien…"
    if kind == "laugh_joke":
        return {"reply": said, "links": [], "source": "talk", "sources": [], "resources": [], "language": lang, "options": [words["another"]]}
    if kind in ("how_are_you", "greeting", "feeling_bad", "im_fine", "me_too"):
        line = _waiting(db, user, lang, context["now"])
        if line:
            said += "\n\n" + (words["pressing"] if kind == "feeling_bad" else words["bridge"]).format(line=line)
    return {"reply": said, "links": [], "source": "talk", "sources": [], "resources": [], "language": lang}


def prefix(db: Session, social: dict, *, user=None, lang: str | None = None) -> str:
    """The word back that comes before the answer, when a message has small talk and a question."""
    lang = lang if lang in _SAY else (social.get("lang") if social.get("lang") in _SAY else "fr")
    words, context = _SAY[lang], _context(db, user, lang)
    kind = next((kind for kind in _LEAD if kind in social["kinds"] and kind in words["prefix"]), None)
    if kind is None:
        return ""
    return _fill(words["prefix"][kind], me=context["me"], name=context["name"], formal=social.get("formal", False),
                 hello=context["hello"], assistant=context["assistant"], day=context["day"])
