"""What Yasmine can do, said by herself: "what can you do?" gives every ability as a card with its moving picture and an
example to try in one tap; "can you send SMS?" gives a plain yes (and how) or a plain "not yet" (and why).

Written by hand in French, English, Arabic and Tunisian; never by a model. What she cannot do is said as plainly as what
she can: she never promises an ability she does not have.
"""
from __future__ import annotations

import re
import unicodedata

# key, icon, colour, motion (the window moves the icon this way), who may use it, keywords ("can you …?"),
# {lang: (title, what it does, an example to try)}
ABILITIES = [
    ("figures", "bi-bar-chart-line-fill", "#2563eb", "rise", "staff", r"chiffres?|figures?|donnees|data|statisti\w+|records?",
     {"fr": ("Vos chiffres, avec vos mots", "Candidats, paiements, classes, séances, formateurs, emails : toute question, dans toute langue, répondue depuis vos données, chaque chiffre vérifié.", "Qui n'a pas encore payé ?"),
      "en": ("Your figures, in your words", "Candidates, payments, classes, sessions, professors, emails: any question in any language, answered from your records, every figure checked.", "Who has not paid yet?"),
      "ar": ("أرقامك بكلماتك", "المترشحون، المدفوعات، الأقسام، الحصص، الأساتذة، الرسائل: أي سؤال بأي لغة، أجيب عنه من بياناتك مع التحقق من كل رقم.", "من لم يدفع بعد؟"),
      "tn": ("Les chiffres mte3ek, b klemek", "Candidats, flous, classes, séances, profs, emails: ay sou2el b ay lougha, njewbek mel données mte3ek w kol ra9m mverifié.", "Chkoun mazel ma khallasch?")}),
    ("cards", "bi-images", "#db2777", "pop", "staff", r"images?|graphiques?|charts?|dessins?|pictures?|visuels?|cartes?|cards?",
     {"fr": ("Facile à lire", "Les chiffres et les listes arrivent en cartes qui bougent : tuiles, barres, anneaux, personnes, dates. Seulement ce que vous avez demandé.", "Combien de candidats avons-nous ?"),
      "en": ("Easy to read", "Figures and lists come as moving cards: tiles, bars, rings, people, dates. Only what you asked for.", "How many candidates do we have?"),
      "ar": ("سهل القراءة", "الأرقام والقوائم تصل في بطاقات متحركة: مربعات، أعمدة، حلقات، أشخاص، تواريخ. فقط ما طلبته.", "كم عدد المترشحين لدينا؟"),
      "tn": ("Sahla bech te9raha", "Les chiffres w les listes yjiw cartes yet7arkou: tuiles, barres, anneaux, nes, dates. Ken elli tlabtou.", "9addech 3andna men candidat?")}),
    ("send", "bi-send-fill", "#16a34a", "fly", "staff", r"envo\w+|send\w*|sms|whats ?app|e ?mails?|mails?|messages?|ab3ath|ecrire|write",
     {"fr": ("J'écris et j'envoie pour vous", "Un email, un WhatsApp ou un SMS à un candidat, une classe entière, un formateur ou l'équipe : je l'écris à partir de vos mots et il part tout de suite, sans changer un chiffre.", "Écris à Fares pour lui rappeler la séance de samedi"),
      "en": ("I write and send for you", "An email, a WhatsApp or an SMS to a candidate, a whole class, a professor or the team: I write it from your words and it goes at once, without changing a figure.", "Write to Fares to remind him of Saturday's session"),
      "ar": ("أكتب وأرسل نيابة عنك", "رسالة بريد أو واتساب أو SMS إلى مترشح أو قسم كامل أو أستاذ أو الفريق: أكتبها من كلماتك وتُرسل فورا، دون تغيير أي رقم.", "اكتبي إلى فارس لتذكيره بحصة السبت"),
      "tn": ("Nekteb w nab3ath fi blastek", "Email, WhatsApp wala SMS l candidat, classe kemla, prof wala l'équipe: nektbou men klemek w yemchi tawa, bla ma nbaddel 7ata ra9m.", "Ab3ath message l Fares 9oulou el séance mta3 sebt tna7at")}),
    ("guide", "bi-map-fill", "#4f46e5", "seek", "all", r"guide\w*|expliqu\w+|explain\w*|cette page|this page|pages?",
     {"fr": ("Je vous guide sur chaque page", "À quoi sert la page, chacune de ses parties, ce que vous pouvez y faire et changer ; puis je vous la montre partie par partie.", "Explique-moi cette page"),
      "en": ("I guide you on every page", "What the page is for, each of its parts, what you can do and change there; then I show it to you part by part.", "Explain this page"),
      "ar": ("أرشدك في كل صفحة", "ما فائدة الصفحة، وكل جزء منها، وما يمكنك فعله وتغييره فيها؛ ثم أريك إياها جزءا جزءا.", "اشرح لي هذه الصفحة"),
      "tn": ("Nwarrik kol page", "El page chnowa tsalla7, kol parti fiha, chnowa tnajjem ta3mel w tbaddel; w ba3d nwarrihelek parti b parti.", "Chnowa najjem na3mel lehna?")}),
    ("go", "bi-signpost-2-fill", "#0ea5e9", "swing", "all", r"ouvr\w+|open|pages?|navigu\w+|navigat\w+|aller|emmen\w+|take me|7ell|visit\w*|tour",
     {"fr": ("Je vous emmène", "Sur n'importe quelle page ou la fiche de quelqu'un, et je reste avec vous. Une visite de toutes les pages, même toute seule.", "Ouvre les paiements"),
      "en": ("I take you there", "To any page or someone's file, and I stay with you. A visit of every page, even by myself.", "Open the payments"),
      "ar": ("آخذك إلى هناك", "إلى أي صفحة أو ملف أي شخص، وأبقى معك. جولة في كل الصفحات، حتى وحدي.", "افتح المدفوعات"),
      "tn": ("Nhezzek ghadi", "L ay page wala fiche mta3 ay wa7ed, w nab9a m3ak. Visite l les pages el kol, 7ata wa7di.", "7ell les paiements")}),
    ("prepare", "bi-pencil-square", "#6366f1", "write", "staff", r"prepar\w+|redig\w+|draft|composer|brouillon",
     {"fr": ("Je prépare un email", "J'ouvre la page des emails déjà remplie pour la personne : vous relisez et vous envoyez.", "Prépare un email pour Fares : rappel de la séance de demain"),
      "en": ("I prepare an email", "I open the email page already filled in for the person: you read it and send it.", "Prepare an email for Fares: reminder of tomorrow's session"),
      "ar": ("أحضّر رسالة", "أفتح صفحة الرسائل مكتوبة للشخص: تقرأها ثم ترسلها.", "حضّر رسالة إلى فارس: تذكير بحصة الغد"),
      "tn": ("N7adher email", "N7ell page mta3 el emails 3amra lel chakhs: ta9raha w tab3athha.", "7adherli email l Fares: rappel mta3 séance ghodwa")}),
    ("remember", "bi-chat-left-heart-fill", "#be123c", "beat", "all", r"souvenir|rappeler de|remember|memoire|memory|conversation|historique|history",
     {"fr": ("Je me souviens", "Notre conversation continue de page en page et d'un jour à l'autre. « Où en étions-nous ? » et je reprends.", "Où en étions-nous ?"),
      "en": ("I remember", "Our conversation goes on from page to page and from one day to the next. “Where were we?” and I pick it up.", "Where were we?"),
      "ar": ("أتذكّر", "محادثتنا تستمر من صفحة إلى أخرى ومن يوم إلى آخر. «أين كنا؟» وأكمل.", "أين كنا؟"),
      "tn": ("Nefker", "El 7dith mte3na ykammel men page l page w men nhar l nhar. « Win wselna? » w nkammel.", "Win wselna?")}),
    ("search", "bi-search-heart", "#0d9488", "seek", "staff", r"cherch\w+|search\w*|recherch\w+|trouv\w+|find|internet|web|google",
     {"fr": ("Je cherche partout", "Dans vos données (personnes, emails, classes, programmes), le journal, les offres d'emploi, et sur le web quand il le faut.", "Quelles compétences demande le marché en data ?"),
      "en": ("I search everywhere", "In your records (people, emails, classes, programs), the journal, the job offers, and on the web when needed.", "Which skills does the job market ask for in data?"),
      "ar": ("أبحث في كل مكان", "في بياناتك (الأشخاص، الرسائل، الأقسام، البرامج)، والجريدة، وعروض الشغل، وعلى الإنترنت عند الحاجة.", "ما المهارات التي يطلبها سوق الشغل في البيانات؟"),
      "tn": ("Nlawej fi kol blasa", "Fil données mte3ek (nes, emails, classes, programmes), el journal, offres d'emploi, w 3al web ki yelzem.", "Chnowa el compétences elli yotlobha el marché fil data?")}),
    ("market", "bi-graph-up-arrow", "#ea580c", "rise", "staff", r"marche|market|emplois?|jobs?|tendances?|trends?|offres?|recrut\w+",
     {"fr": ("Le marché et les tendances", "Ce que demandent les offres d'emploi, ce qui est nouveau dans chaque domaine, et quoi ajouter aux programmes.", "Qu'est-ce qui est tendance en IA ?"),
      "en": ("The market and trends", "What job offers ask for, what is new in each field, and what to add to the programs.", "What is trending in AI?"),
      "ar": ("السوق والتوجهات", "ما تطلبه عروض الشغل، والجديد في كل مجال، وما يُضاف إلى البرامج.", "ما الرائج في الذكاء الاصطناعي؟"),
      "tn": ("El marché w el tendances", "Chnowa yotlbou el offres, chnowa jdid fi kol domaine, w chnowa nzidou lel programmes.", "Chnowa tendance fil IA?")}),
    ("keep", "bi-person-heart", "#e11d48", "beat", "staff", r"abandon\w*|drop\w*|risque\w*|risk\w*|ybattel",
     {"fr": ("Qui risque d'abandonner", "Qui, pourquoi (absences, questions sans réponse, ce qu'il doit) et quoi faire pour chacun.", "Qui risque d'abandonner ?"),
      "en": ("Who may drop out", "Who, why (absences, unanswered questions, what they owe) and what to do for each.", "Who may drop out?"),
      "ar": ("من قد ينقطع", "من، ولماذا (الغياب، الأسئلة دون إجابة، ما عليه) وماذا تفعل مع كل واحد.", "من قد ينقطع عن التكوين؟"),
      "tn": ("Chkoun ynajjem ybattel", "Chkoun, 3lech (ghiyabet, as2la bla jweb, chnowa 3lih) w chnowa ta3mel m3a kol wa7ed.", "Chkoun ynajjem ybattel?")}),
    ("summary", "bi-text-paragraph", "#9333ea", "write", "staff", r"resum\w+|summar\w+|synthes\w+",
     {"fr": ("Je résume", "Un candidat, une discussion, une classe, la journée, la semaine, la page ouverte ou un texte collé.", "Résume cette page"),
      "en": ("I sum up", "A candidate, a discussion, a class, the day, the week, the page open or a pasted text.", "Sum up this page"),
      "ar": ("ألخّص", "مترشحا، محادثة، قسما، اليوم، الأسبوع، الصفحة المفتوحة أو نصا تلصقه.", "لخّص هذه الصفحة"),
      "tn": ("Nlakhes", "Candidat, conversation, classe, el nhar, el jem3a, el page el m7oula wala texte.", "Lakhasli el page hedhi")}),
    ("system", "bi-sliders2", "#0f766e", "spin", "admin", r"parametr\w+|settings?|reglag\w+|configur\w+|chang\w+ (?:le|les|the) (?:systeme|system)",
     {"fr": ("Je change le système", "Un réglage, un rappel, les prix, mon apparence… puis « annule » si besoin.", "Éteins le rappel de séance"),
      "en": ("I change the system", "A setting, a reminder, the prices, my look… then “undo” if needed.", "Turn off the session reminder"),
      "ar": ("أغيّر النظام", "إعدادا، تذكيرا، الأسعار، مظهري… ثم «تراجع» عند الحاجة.", "أوقف تذكير الحصة"),
      "tn": ("Nbaddel el système", "Réglage, rappel, les prix, chakli… w ba3d « annule » ken yelzem.", "Tfi el rappel mta3 el séance")}),
    ("documents", "bi-file-earmark-pdf-fill", "#dc2626", "pop", "staff", r"pdf|documents?|brochures?|catalogue|excel|csv",
     {"fr": ("Des PDF", "Un programme, tous les programmes, en détail ou en bref, ou n'importe laquelle de mes réponses.", "Envoie-moi tous les programmes en PDF"),
      "en": ("PDFs", "A program, all programs, in detail or in short, or any of my answers.", "Send me all the programs in PDF"),
      "ar": ("ملفات PDF", "برنامج، كل البرامج، بالتفصيل أو باختصار، أو أي إجابة من إجاباتي.", "أرسل لي كل البرامج في PDF"),
      "tn": ("PDF", "Programme, les programmes el kol, bel tafsil wala mlakhsin, wala ay jweb mte3i.", "Ab3athli les programmes el kol en PDF")}),
    ("marketing", "bi-megaphone-fill", "#f59e0b", "wiggle", "staff", r"publi\w+|posts?|marketing|reseaux|social|instagram|facebook|tiktok",
     {"fr": ("Quoi publier", "Les idées de la semaine, les meilleures heures, ce qui marche dans les publications.", "Qu'est-ce que je publie cette semaine ?"),
      "en": ("What to post", "The week's ideas, the best hours, what works in posts.", "What should I post this week?"),
      "ar": ("ماذا أنشر", "أفكار الأسبوع، أفضل الأوقات، ما ينجح في المنشورات.", "ماذا أنشر هذا الأسبوع؟"),
      "tn": ("Chnowa nonchrou", "Idées mta3 el jem3a, a7sen wa9t, w chnowa yemchi fil publications.", "Chnowa nonchrou el jem3a hedhi?")}),
    ("ask", "bi-patch-question-fill", "#7c3aed", "beat", "all", r"(?:poser|ask) (?:des |me |you )?questions?|clarif\w+|comprendre|understand",
     {"fr": ("Je demande quand c'est flou", "Une question courte avec des choix à toucher, pour répondre exactement à ce que vous voulez.", "Combien ?"),
      "en": ("I ask when it's unclear", "A short question with choices to tap, to answer exactly what you want.", "How many?"),
      "ar": ("أسأل عندما يكون الأمر غامضا", "سؤال قصير مع اختيارات بلمسة، لأجيب بالضبط عما تريده.", "كم؟"),
      "tn": ("Nes2el ki tkoun mech wadh7a", "Sou2el 9sir b choix tekbeshom, bech njewbek 3al 7aja elli t7ebha bedhabt.", "9addech?")}),
    ("languages", "bi-translate", "#0891b2", "swing", "all", r"langues?|languages?|arabe|arabic|anglais|english|francais|french|tunisien|tunisian|tounsi|derja",
     {"fr": ("Votre langue", "Français, anglais, arabe ou tounsi : je réponds dans celle de votre question, ou celle choisie en haut.", "Chnowa a7wel el classes?"),
      "en": ("Your language", "French, English, Arabic or Tunisian: I answer in the one of your question, or the one chosen at the top.", "Chnowa a7wel el classes?"),
      "ar": ("لغتك", "الفرنسية أو الإنجليزية أو العربية أو التونسية: أجيب بلغة سؤالك، أو باللغة المختارة في الأعلى.", "Chnowa a7wel el classes?"),
      "tn": ("Loghtek", "Français, anglais, 3arbi wala tounsi: njewbek b loughet sou2alek, wala elli ekhtartha el fou9.", "Comment vont les classes ?")}),
    ("joke", "bi-emoji-laughing-fill", "#facc15", "wiggle", "all", r"blagues?|jokes?|rire|laugh|drole|funny|nokta|n9at|نكت\w*",
     {"fr": ("Un peu d'humour", "Une blague quand la journée est longue (et une autre si vous insistez).", "Raconte-moi une blague"),
      "en": ("A bit of fun", "A joke when the day is long (and another if you insist).", "Tell me a joke"),
      "ar": ("قليل من المرح", "نكتة عندما يطول اليوم (وأخرى إن ألححت).", "احكي لي نكتة"),
      "tn": ("Chwaya dha7k", "Nokta ki ytoul el nhar (w okhra ken t7eb).", "A3tini nokta")}),
]
# What she cannot do (yet): said plainly, with why. (keywords, {lang: sentence})
CANNOT = [
    (r"voix|vocal\w*|micro\w*|dicte\w*|voice|speak to you|talk to you out loud|parler a voix",
     {"fr": "Pas encore m'écouter parler : la reconnaissance de la voix du navigateur envoie le son en ligne, il faut votre accord d'abord. Je peux déjà lire mes réponses à voix haute (dans mes réglages).",
      "en": "Not listen to your voice yet: the browser's speech recognition sends the sound online, so it needs your yes first. I can already read my answers aloud (in my settings).",
      "ar": "لا أستطيع بعد الاستماع إلى صوتك: التعرّف على الصوت في المتصفح يرسل الصوت عبر الإنترنت، ويحتاج موافقتك أولا. أستطيع قراءة إجاباتي بصوت عال (في إعداداتي).",
      "tn": "Mazelt ma nnajjemch nesma3 sotek: el navigateur yab3ath el sot 3al internet, yelzem mouwaf9tek 9bal. Ama najjem na9ra jwebi b sot (fil réglages mte3i)."}),
    (r"appel\w*|telephon\w*|phone calls?|call (?:them|him|her|candidates?)",
     {"fr": "Pas passer d'appels téléphoniques : je vous donne le numéro (+216) et le bouton pour appeler vous-même.",
      "en": "Not make phone calls: I give you the number (+216) and the button to call yourself.",
      "ar": "لا أجري مكالمات هاتفية: أعطيك الرقم (+216) والزر لتتصل بنفسك.",
      "tn": "Ma nnajjemch na3mel appel: na3tik el noumrou (+216) w el bouton bech t3ayyet enti."}),
    (r"publi\w+ (?:pour moi |a ma place )?sur (?:facebook|instagram|linkedin|tiktok)|post (?:it )?(?:for me )?on (?:facebook|instagram|linkedin|tiktok)",
     {"fr": "Pas publier moi-même sur Facebook, Instagram ou LinkedIn : je prépare la publication (texte, image, vidéo), vous la postez.",
      "en": "Not post on Facebook, Instagram or LinkedIn myself: I prepare the post (text, picture, video), you post it.",
      "ar": "لا أنشر بنفسي على فيسبوك أو إنستغرام أو لينكدإن: أحضّر المنشور (نص، صورة، فيديو)، وأنت تنشره.",
      "tn": "Ma nonchorch ana 3al Facebook, Instagram wala LinkedIn: n7adher el publication (texte, taswira, vidéo), w enti tonchorha."}),
    (r"linkedin",
     {"fr": "Pas lire LinkedIn par moi-même (leurs règles l'interdisent) : collez-moi le texte d'une offre ou d'un post, je l'analyse.",
      "en": "Not read LinkedIn by myself (their rules forbid it): paste me the text of an offer or a post, I analyse it.",
      "ar": "لا أقرأ لينكدإن بنفسي (قواعدهم تمنع ذلك): الصق لي نص عرض أو منشور وأحلّله.",
      "tn": "Ma na9rach LinkedIn wa7di (9wanin mte3hom yemn3ou): 7ottli el texte mta3 offre wala post, w n7allel."}),
    (r"rembours\w+|refund\w*|pay(?:er)? (?:a ma place|for me)",
     {"fr": "Pas rembourser ni payer : l'argent passe toujours par vous. Les demandes de remboursement vous arrivent toujours.",
      "en": "Not refund or pay: money always goes through you. Refund requests always come to you.",
      "ar": "لا أسترجع المال ولا أدفع: المال يمر دائما عبرك. طلبات الاسترجاع تصلك دائما.",
      "tn": "Ma nraja3ch flous w ma nkhallasch: el flous dima t3addi 3lik. Talabet el remboursement dima tjik."}),
    (r"(?:qui parle|who speaks|qui a parle) (?:dans|in) (?:la |the )?(?:reunion|meeting|seance|session)|voir dans (?:la )?(?:reunion|meet)",
     {"fr": "Pas voir à l'intérieur d'une réunion en ligne (qui parle) : le formateur le note sur la feuille de présence.",
      "en": "Not see inside an online meeting (who speaks): the professor notes it on the presence sheet.",
      "ar": "لا أرى داخل الاجتماع عبر الإنترنت (من يتكلم): الأستاذ يسجّل ذلك في ورقة الحضور.",
      "tn": "Ma nchoufch dakhel el réunion (chkoun yahki): el prof yektbou fil feuille de présence."}),
]
HEAD = {"fr": "Voici ce que je sais faire ; touchez une carte pour l'essayer :", "en": "Here is what I can do; tap a card to try it:",
        "ar": "هذا ما أستطيع فعله؛ المس بطاقة لتجربتها:", "tn": "Hedha chnowa najjem na3mel; ekbes 3la carte bech tjarrabha:"}
NOT_YET = {"fr": "Ce que je ne fais pas (encore)", "en": "What I don't do (yet)", "ar": "ما لا أفعله (بعد)", "tn": "Chnowa ma na3melch (mazel)"}
YES = {"fr": "Oui ! {what}", "en": "Yes! {what}", "ar": "نعم! {what}", "tn": "Ey! {what}"}
NO = {"fr": "Non, {what}", "en": "No: {what}", "ar": "لا، {what}", "tn": "Le, {what}"}
TRY = {"fr": "Essayer", "en": "Try it", "ar": "جرّب", "tn": "Jarreb"}



def _marks_off(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _rx(pattern: str) -> re.Pattern:
    """A word list read on folded words: its Arabic letters folded the same way (أ → ا)."""
    return re.compile(_marks_off(pattern))


_WANTS = _rx(
    r"(?<![\w])(?:what can you do|what do you do|what can i ask(?: you)?|what are you able to do|how can you help|what are your (?:capabilities|skills|abilities|powers)|"
    r"your (?:capabilities|abilities|skills)|que (?:peux|sais)[- ]tu faire|qu est[- ]ce que tu (?:peux|sais) faire|tu sers a quoi|que peut[- ]on te demander|"
    r"comment (?:peux|pourrais)[- ]tu m aider|tes (?:capacites|competences|pouvoirs|fonctionnalites)|quelles sont tes (?:capacites|competences|fonctionnalites)|"
    r"chnowa (?:tnajem|tnajjem|ta3ref) ta3mel|chnia (?:tnajem|tnajjem) ta3mel|kifech tnajem t3awenni|"
    r"ماذا (?:يمكنك|تستطيعين|تستطيع) (?:أن )?(?:تفعلي|تفعل)|ما هي (?:قدراتك|مهاراتك)|قدراتك|شنوة تنجم تعمل|شنوا تنجم تعمل)(?![\w])"
    r"|^\W*(?:help|aide|help me|aidez[- ]moi|capabilities|abilities|capacites)\W*$")
_CAN_YOU = _rx(
    r"^\W*(?:(?:est[- ]ce que|dis[- ]moi)\s+)?(?:tu peux|peux[- ]tu|tu sais|sais[- ]tu|tu es capable de|es[- ]tu capable de|vous pouvez|pouvez[- ]vous|"
    r"can you|could you|are you able to|do you know how to|tnajem|tnajjem|ta3ref|tnajmi|تقدر|تستطيع|تستطيعين|هل يمكنك|يمكنك|تنجم)\b")
_DATA_QUESTION = _rx(
    r"(?<![\w])(?:qui|combien|quand|quel(?:le)?s?|lequel|who|how many|how much|when|which|chkoun|9addech|wa9tech|ama|amma|من|كم|متى|أي)(?![\w])")


# After "can you": a word that points at something precise ("me", "the programs", "Fares's") makes it a request, not a
# question about what she can do ("tu peux m'envoyer les programmes ?" is done, not described).
_REQUEST = re.compile(r"(?<![\w])(?:me|m|moi|nous|lui|leur|le|la|les|l|ce|cette|ces|mon|ma|mes|ton|ta|tes|son|sa|ses|the|this|that|these|"
                      r"those|my|your|his|her|their|us|him|them|it|li|lia)(?![\w])")


def _fold(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", _marks_off(text).lower()).replace("_", " ").split())


def _for(user, who: str) -> bool:
    """Whether this person has that ability: admin-only ones, the team's, everyone's."""
    if user is None or getattr(user, "is_admin", False):
        return True
    if getattr(user, "is_professor", False):
        return who == "all"
    return who in ("all", "staff")


def _lang(lang: str | None) -> str:
    return lang if lang in HEAD else "fr"


def _card(item, lang: str) -> dict:
    key, icon, colour, motion, _, _, said = item
    title, what, example = said[lang]
    return {"key": key, "icon": icon, "colour": colour, "motion": motion, "title": title, "text": what, "ask": example}


def wants(message: str) -> bool:
    """"What can you do?" in any of her languages."""
    return bool(_WANTS.search(_fold(message)))


def answer(db, message: str = "", user=None, lang: str | None = None) -> dict:
    """Every ability she has for this person, as cards to try, then what she does not do (yet)."""
    lang = _lang(lang)
    cards = [_card(item, lang) for item in ABILITIES if _for(user, item[4])]
    lines = [HEAD[lang]] + [f"• {card['title']} — {card['text']}" for card in cards]
    lines += ["", NOT_YET[lang] + " :" if lang == "fr" else NOT_YET[lang] + ":"] + [f"• {said[lang]}" for _, said in CANNOT[:3]]
    return {"reply": "\n".join(lines), "short": HEAD[lang], "source": "guide", "links": [], "sources": [], "resources": [], "scene": "assistant",
            "cards": [{"kind": "abilities", "covers": True, "items": cards, "try": TRY[lang],
                       "cannot": {"title": NOT_YET[lang], "items": [said[lang] for _, said in CANNOT[:3]]}}],
            "suggestions": [card["ask"] for card in cards[:4]]}


def can_you(message: str) -> bool:
    """"Can you send SMS?", "tu peux m'emmener sur une page ?": a question about one of her abilities, not an order."""
    text = _fold(message)
    asking = _CAN_YOU.match(text)
    if not asking or len(text.split()) > 12 or _DATA_QUESTION.search(text) or _REQUEST.search(text[asking.end():]):
        return False
    from app.services import chatbot_send

    order = chatbot_send.parse(message)
    if order and order.get("what"):
        return False                      # "can you send an SMS to Fares saying …": an order, done by its rules
    return _which(text) is not None


def _which(text: str):
    for pattern, said in CANNOT:
        if re.search(_marks_off(rf"(?<![\w])(?:{pattern})(?![\w])"), text):
            return ("cannot", said)
    for item in ABILITIES:
        if re.search(_marks_off(rf"(?<![\w])(?:{item[5]})(?![\w])"), text):
            return ("can", item)
    return None


def answer_can_you(db, message: str, user=None, lang: str | None = None) -> dict:
    """Yes (what she does and an example to try), or no (what she does instead)."""
    lang = _lang(lang)
    kind, found = _which(_fold(message)) or ("cannot", None)
    if kind == "cannot" and found is not None:
        what = found[lang]
        what = what[0].lower() + what[1:] if lang in ("fr", "en") else what
        return {"reply": NO[lang].format(what=what), "source": "guide", "links": [], "sources": [], "resources": [], "scene": "assistant"}
    if found is None or not _for(user, found[4]):
        return answer(db, message, user=user, lang=lang)
    card = _card(found, lang)
    return {"reply": YES[lang].format(what=card["text"]), "source": "guide", "links": [], "sources": [], "resources": [], "scene": "assistant",
            "cards": [{"kind": "abilities", "covers": False, "items": [card], "try": TRY[lang]}], "options": [card["ask"]]}
