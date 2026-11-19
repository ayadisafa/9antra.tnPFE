"""What is new and how to test it, and how to test every functionality (System › Control room): each test is a few steps
with the page to open, what you should see, and whether it sends something to a person (then use a test candidate with
your own email and phone). The admin marks each one "It works" or "Problem" (with what), kept in `test_marks`.

The texts are written by hand in English, French and Arabic, side by side; the page shows the language it is in.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.services import ui_words


def L(en: str, fr: str, ar: str) -> dict:
    return {"en": en, "fr": fr, "ar": ar}


def say(text) -> str:
    if isinstance(text, dict):
        return text.get(ui_words.language()) or text["en"]
    return text or ""


# ------------------------------------------------------------------
# What is new, newest first
# ------------------------------------------------------------------

NEWS = [
    {"date": "2026-11-17", "title": L("Frequent questions, FAQ posts in your design, suggestions that follow the market",
                                      "Questions fréquentes, posts FAQ dans votre style, suggestions qui suivent le marché",
                                      "الأسئلة المتكررة، منشورات الأسئلة بتصميمك، اقتراحات تتبع السوق"),
     "items": [
         {"key": "new:faq", "url": "/inbox/knowledge#faq", "sends": False,
          "title": L("The Summer Camp FAQ, answered by every assistant", "La FAQ du Summer Camp, répondue par chaque assistant",
                     "أسئلة Summer Camp، يجيب عنها كل مساعد"),
          "steps": [L("Open What the assistant knows › Frequent questions: the seven questions of your carousel are there.",
                      "Ouvrez Ce que sait l'assistant › Questions fréquentes : les sept questions de votre carrousel y sont.",
                      "افتح ما يعرفه المساعد › الأسئلة المتكررة: الأسئلة السبعة لمنشورك موجودة."),
                    L("Ask Yasmine “kifech el wa9t fel summer camp ?”, then “quels sont les horaires ?”.",
                      "Demandez à Yasmine « kifech el wa9t fel summer camp ? », puis « quels sont les horaires ? ».",
                      "اسأل ياسمين «kifech el wa9t fel summer camp ؟» ثم «ما هي المواعيد؟».")],
          "expect": L("The first gets the Summer Camp's Saturdays and Wednesdays; the second, without the Summer Camp, never does.",
                      "La première reçoit les samedis et mercredis du Summer Camp ; la seconde, sans le Summer Camp, jamais.",
                      "الأول يحصل على أيام السبت والأربعاء لـSummer Camp؛ والثاني، دون ذكره، لا يحصل عليها أبدا.")},
         {"key": "new:faq-posts", "url": "/social#faq", "sends": False,
          "title": L("FAQ posts in your design, a theme per season", "Posts FAQ dans votre style, un thème par saison", "منشورات الأسئلة بتصميمك، لكل موسم طابعه"),
          "steps": [L("Open Social media › FAQ posts.", "Ouvrez Réseaux sociaux › Posts FAQ.", "افتح مواقع التواصل › منشورات الأسئلة."),
                    L("Change the season, then download the carousel.", "Changez la saison, puis téléchargez le carrousel.", "غيّر الموسم ثم نزّل المنشور.")],
          "expect": L("Your slides: the brick wall, the season's sticker, the orange bar, the answer on pink, your number; a last “contact us” slide and the caption.",
                      "Vos slides : le mur de briques, l'autocollant de la saison, la barre orange, la réponse sur fond rose, votre numéro ; une dernière « contact us » et le texte.",
                      "شرائحك: جدار الطوب، ملصق الموسم، الشريط البرتقالي، الإجابة على الوردي، رقمك؛ شريحة أخيرة «contact us» والنص.")},
         {"key": "new:faq-read", "url": "/inbox/knowledge#faq", "sends": False,
          "title": L("A carousel's pictures read by themselves", "Les images d'un carrousel lues toutes seules", "صور المنشور تُقرأ وحدها"),
          "steps": [L("In Frequent questions, press “Read a carousel's pictures” and choose the slides of a post.",
                      "Dans Questions fréquentes, appuyez sur « Lire les images d'un carrousel » et choisissez les slides d'un post.",
                      "في الأسئلة المتكررة اضغط «اقرأ صور منشور» واختر شرائح منشور.")],
          "expect": L("Each slide's question and answer, ready to check and keep.", "La question et la réponse de chaque slide, prêtes à vérifier et garder.",
                      "سؤال وإجابة كل شريحة، جاهزان للتحقق والحفظ.")},
         {"key": "new:market-ideas", "url": "/system/control#ideas", "sends": False,
          "title": L("Suggestions that follow the market", "Des suggestions qui suivent le marché", "اقتراحات تتبع السوق"),
          "steps": [L("Open Control room › Suggestions and press “Read the market again now”.", "Ouvrez Salle de contrôle › Suggestions et appuyez sur « Relire le marché maintenant ».",
                      "افتح غرفة المراقبة › الاقتراحات واضغط «أعد قراءة السوق الآن».")],
          "expect": L("A minute later, the ranking with the articles of training news that support each idea.",
                      "Une minute plus tard, le classement avec les articles de la formation qui soutiennent chaque idée.",
                      "بعد دقيقة، الترتيب مع مقالات أخبار التكوين التي تدعم كل فكرة.")},
     ]},
    {"date": "2026-11-16", "title": L("Control room, and answers that go by themselves", "Salle de contrôle, et réponses qui partent toutes seules",
                                      "غرفة المراقبة، وردود تُرسل وحدها"),
     "items": [
         {"key": "new:control-room", "url": "/system/control", "sends": False,
          "title": L("The live map of the application", "La carte vivante de l'application", "الخريطة الحية للتطبيق"),
          "steps": [L("Open System › Control room.", "Ouvrez Système › Salle de contrôle.", "افتح النظام › غرفة المراقبة."),
                    L("Leave it open and record a payment or answer an email in another tab.",
                      "Laissez-la ouverte et enregistrez un paiement ou répondez à un email dans un autre onglet.",
                      "اتركها مفتوحة وسجّل دفعة أو أجب عن بريد في لسان آخر.")],
          "expect": L("Light runs along the lines between the parts that worked; their figures change by themselves.",
                      "La lumière court sur les lignes entre les parties qui ont travaillé ; leurs chiffres changent tout seuls.",
                      "يجري الضوء على الخطوط بين الأجزاء التي عملت؛ وتتغير أرقامها وحدها.")},
         {"key": "new:reword-send", "url": "/inbox", "sends": True,
          "title": L("Reword and send, with no other click", "Reformuler et envoyer, sans autre clic", "إعادة الصياغة والإرسال دون نقرة أخرى"),
          "steps": [L("Open a discussion with your test candidate in the Inbox.", "Ouvrez une discussion avec votre candidat de test dans la boîte de réception.",
                      "افتح محادثة مع مترشح التجربة في صندوق الوارد."),
                    L("Turn on “Send by itself when well reworded”, write “ey samedi 9h” and press “Reword and send”.",
                      "Activez « Envoyer tout seul si bien reformulé », écrivez « ey samedi 9h » et appuyez sur « Reformuler et envoyer ».",
                      "فعّل «يُرسل وحده إذا أُحسنت الصياغة»، اكتب «ey samedi 9h» واضغط «إعادة الصياغة والإرسال»."),
                    L("Try again and press “Stop” during the 3 seconds.", "Recommencez et appuyez sur « Arrêter » pendant les 3 secondes.",
                      "أعد المحاولة واضغط «إيقاف» خلال الثواني الثلاث.")],
          "expect": L("A green bar for 3 seconds, then “Answer sent”. With Stop, nothing goes. A changed figure or an added name waits, with the reason in red.",
                      "Une barre verte pendant 3 secondes, puis « Réponse envoyée ». Avec Arrêter, rien ne part. Un chiffre changé ou un nom ajouté attend, avec la raison en rouge.",
                      "شريط أخضر لثلاث ثوان، ثم «تم إرسال الرد». مع الإيقاف لا يُرسل شيء. الرقم المتغير أو الاسم المضاف ينتظر مع السبب بالأحمر.")},
     ]},
    {"date": "2026-11-15", "title": L("Before you answer, Reword everywhere, Yasmine writes", "Avant de répondre, Reformuler partout, Yasmine écrit",
                                      "قبل أن تجيب، إعادة الصياغة في كل مكان، ياسمين تكتب"),
     "items": [
         {"key": "new:about-say", "url": "/inbox", "sends": False,
          "title": L("About the candidate, or what to say", "Le candidat, ou quoi lui dire", "عن المترشح، أو ماذا تقول له"),
          "steps": [L("Open any discussion in the Inbox.", "Ouvrez n'importe quelle discussion dans la boîte de réception.", "افتح أي محادثة في صندوق الوارد."),
                    L("Switch between “About” and “What to say”; tap one sentence.", "Passez de « À propos de » à « Quoi lui dire » ; touchez une phrase.",
                      "بدّل بين «حول» و«ماذا تقول له»؛ انقر على جملة.")],
          "expect": L("Their programs, payments, class, presence and feelings; the sentence goes into your answer.",
                      "Ses formations, paiements, classe, présence et ressentis ; la phrase va dans votre réponse.",
                      "برامجه ودفعاته وقسمه وحضوره ومشاعره؛ وتُضاف الجملة إلى ردك.")},
         {"key": "new:reword-bar", "url": "/inbox/compose", "sends": False,
          "title": L("Reword under every message box", "Reformuler sous chaque case de message", "إعادة الصياغة تحت كل خانة رسالة"),
          "steps": [L("In Compose, Team chat or Reminders, write “ija ghodwa m3a 9h”.", "Dans Écrire, la discussion d'équipe ou les rappels, écrivez « ija ghodwa m3a 9h ».",
                      "في كتابة رسالة أو محادثة الفريق أو التذكيرات، اكتب «ija ghodwa m3a 9h»."),
                    L("Press “Reword”, then “Put my words back”.", "Appuyez sur « Reformuler », puis « Remettre mes mots ».", "اضغط «إعادة الصياغة» ثم «أرجع كلماتي».")],
          "expect": L("A proper message in French, 9h kept; your words come back with one tap. Nothing is sent.",
                      "Un vrai message en français, 9h gardé ; vos mots reviennent d'un appui. Rien n'est envoyé.",
                      "رسالة سليمة بالفرنسية مع الإبقاء على 9h؛ وتعود كلماتك بنقرة. لا يُرسل شيء.")},
         {"key": "new:yasmine-write", "url": "/", "sends": True,
          "title": L("Yasmine writes: “write to … winik”", "Yasmine écrit : « écris à … winik »", "ياسمين تكتب: «اكتب لـ… وينك»"),
          "steps": [L("Open Yasmine and write “write to <your test candidate's first name> winik”.",
                      "Ouvrez Yasmine et écrivez « écris à <prénom de votre candidat de test> winik ».",
                      "افتح ياسمين واكتب «اكتب لـ<اسم مترشح التجربة> وينك»."),
                    L("Try with a first name several people have, then the same order again.",
                      "Essayez avec un prénom que plusieurs personnes ont, puis la même demande encore.",
                      "جرّب باسم يحمله عدة أشخاص، ثم الطلب نفسه مرة أخرى.")],
          "expect": L("She says “winik = où es-tu”, rewords and sends. Several people: one tap each; the second time she takes the one you chose and says so.",
                      "Elle dit « winik = où es-tu », reformule et envoie. Plusieurs personnes : un appui chacune ; la deuxième fois elle prend celle choisie et le dit.",
                      "تقول «winik = où es-tu»، تعيد الصياغة وترسل. عدة أشخاص: نقرة لكل واحد؛ وفي المرة الثانية تختار من اخترته وتقول ذلك.")},
     ]},
    {"date": "2026-11-15", "title": L("A class never runs empty", "Une classe ne tourne jamais vide", "لا يعمل قسم فارغا أبدا"),
     "items": [
         {"key": "new:empty-class", "url": "/classes/", "sends": True,
          "title": L("An emptied class removes itself", "Une classe vidée se supprime d'elle-même", "القسم الذي أُفرغ يُحذف تلقائيا"),
          "steps": [L("Make a test class that has not started, with only your test candidate in it.",
                      "Créez une classe de test pas encore commencée, avec seulement votre candidat de test.",
                      "أنشئ قسم تجربة لم يبدأ بعد، فيه مترشح التجربة فقط."),
                    L("Move your test candidate to another class, then wait 30 minutes.", "Déplacez votre candidat de test vers une autre classe, puis attendez 30 minutes.",
                      "انقل مترشح التجربة إلى قسم آخر، ثم انتظر 30 دقيقة.")],
          "expect": L("The class page first says why and when; then the class is gone, a notification gives the reason, its professor is told.",
                      "La page de la classe dit d'abord pourquoi et quand ; puis la classe disparaît, une notification donne la raison, son professeur est prévenu.",
                      "تقول صفحة القسم أولا السبب والموعد؛ ثم يختفي القسم، ويذكر إشعار السبب، ويُعلَم أستاذه.")},
     ]},
    {"date": "2026-11-15", "title": L("Reminder timers and kinds", "Minuteurs et types de rappels", "مؤقتات التذكيرات وأنواعها"),
     "items": [
         {"key": "new:reminder-timer", "url": "/reminders", "sends": True,
          "title": L("A reminder in 2 minutes", "Un rappel dans 2 minutes", "تذكير بعد دقيقتين"),
          "steps": [L("In Reminders, write one to your test candidate and choose “In” 2 minutes.", "Dans Rappels, écrivez-en un à votre candidat de test et choisissez « Dans » 2 minutes.",
                      "في التذكيرات، اكتب واحدا لمترشح التجربة واختر «بعد» دقيقتين."),
                    L("Tap a suggested kind with the automatic icon.", "Touchez un type suggéré avec l'icône automatique.", "انقر على نوع مقترح عليه رمز التلقائي.")],
          "expect": L("It goes 2 minutes later; the kind fills the form, or is set up on the automatic board in one click.",
                      "Il part 2 minutes plus tard ; le type remplit le formulaire, ou se met sur le tableau automatique en un clic.",
                      "يُرسل بعد دقيقتين؛ والنوع يملأ الاستمارة أو يُضاف إلى اللوحة التلقائية بنقرة.")},
     ]},
    {"date": "2026-11-14", "title": L("Professors, absences, monthly prices", "Professeurs, absences, prix au mois", "الأساتذة والغيابات والأسعار الشهرية"),
     "items": [
         {"key": "new:absence", "url": "/professors", "sends": True,
          "title": L("A professor asks to be absent", "Un professeur demande une absence", "أستاذ يطلب غيابا"),
          "steps": [L("As a professor (Settings › Team, “See as”), ask an absence for a few days.", "En professeur (Réglages › Équipe, « Voir comme »), demandez une absence de quelques jours.",
                      "كأستاذ (الإعدادات › الفريق، «عرض كـ»)، اطلب غيابا لبضعة أيام."),
                    L("On Professors, accept or refuse it.", "Dans Professeurs, acceptez-la ou refusez-la.", "في الأساتذة، اقبله أو ارفضه.")],
          "expect": L("Accepted: each session gets a replacement or your choice. Refused: the professor answers with one click.",
                      "Acceptée : chaque séance a un remplaçant ou votre choix. Refusée : le professeur répond d'un clic.",
                      "عند القبول: لكل حصة بديل أو اختيارك. عند الرفض: يجيب الأستاذ بنقرة.")},
     ]},
]


# ------------------------------------------------------------------
# Every page: how to test it (the page's own explanation comes from guides.py)
# ------------------------------------------------------------------

PAGES = {
    "/": ([L("Open the dashboard.", "Ouvrez le tableau de bord.", "افتح لوحة القيادة."),
           L("Press the first thing to do, or “Not now”.", "Appuyez sur la première chose à faire, ou « Pas maintenant ».", "اضغط على أول شيء يجب فعله أو «ليس الآن».")],
          L("What waits for you, with why; it disappears once done.", "Ce qui vous attend, avec pourquoi ; cela disparaît une fois fait.", "ما ينتظرك مع السبب؛ ويختفي بعد إنجازه.")),
    "/inbox": ([L("Press “⋯” › “Try the assistant with a sample email”.", "Appuyez sur « ⋯ » › « Essayer l'assistant avec un email d'exemple ».", "اضغط «⋯» › «جرّب المساعد ببريد مثال»."),
                L("Write a question about prices, then send.", "Écrivez une question sur les prix, puis envoyez.", "اكتب سؤالا عن الأسعار ثم أرسل.")],
               L("A test discussion: answered alone when sure, or a draft for you. Nothing leaves.", "Une discussion de test : répondue seule si sûre, sinon un brouillon pour vous. Rien ne part.",
                 "محادثة تجربة: يُجاب عنها وحدها إن كان واثقا، وإلا مسودة لك. لا يخرج شيء.")),
    "/payments": ([L("Record a payment for your test candidate.", "Enregistrez un paiement pour votre candidat de test.", "سجّل دفعة لمترشح التجربة."),
                   L("Drop a receipt picture in “Payment proofs”.", "Déposez la photo d'un reçu dans « Preuves de paiement ».", "ضع صورة وصل في «إثباتات الدفع».")],
                  L("The amount left changes; once paid in full they are placed in a class. The receipt is read and checked.",
                    "Le reste à payer change ; payé en entier, il est placé dans une classe. Le reçu est lu et vérifié.",
                    "يتغير المبلغ المتبقي؛ وعند الدفع الكامل يُوضع في قسم. ويُقرأ الوصل ويُتحقق منه.")),
    "/reminders": ([L("Make a reminder for your test candidate in 2 minutes.", "Créez un rappel pour votre candidat de test dans 2 minutes.", "أنشئ تذكيرا لمترشح التجربة بعد دقيقتين."),
                    L("Switch an automatic reminder off, then on.", "Coupez un rappel automatique, puis rallumez-le.", "أوقف تذكيرا تلقائيا ثم شغّله.")],
                   L("It goes at the time; the board shows each automatic reminder with its timer.", "Il part à l'heure ; le tableau montre chaque rappel automatique avec son minuteur.",
                     "يُرسل في وقته؛ وتعرض اللوحة كل تذكير تلقائي مع مؤقته.")),
    "/candidate/all": ([L("Search your test candidate and open their file.", "Cherchez votre candidat de test et ouvrez son dossier.", "ابحث عن مترشح التجربة وافتح ملفه."),
                        L("Look at each tab: programs, learning, behaviour, emails.", "Regardez chaque onglet : formations, apprentissage, comportement, emails.",
                          "انظر في كل لسان: البرامج، التعلم، السلوك، الرسائل.")],
                       L("One card per person with the next step; the file holds everything about them.", "Une carte par personne avec la prochaine étape ; le dossier contient tout sur elle.",
                         "بطاقة لكل شخص مع الخطوة التالية؛ والملف يحوي كل شيء عنه.")),
    "/classes/": ([L("Open a class, then “Parameters”.", "Ouvrez une classe, puis « Paramètres ».", "افتح قسما ثم «الإعدادات»."),
                   L("Open its presence sheet and its course.", "Ouvrez sa feuille de présence et son cours.", "افتح ورقة حضوره ودرسه.")],
                  L("Its sessions, professor and candidates; changes are saved and the candidates told when dates move.",
                    "Ses séances, son professeur et ses candidats ; les changements sont gardés et les candidats prévenus si les dates bougent.",
                    "حصصه وأستاذه ومترشحوه؛ تُحفظ التغييرات ويُعلَم المترشحون إذا تغيرت المواعيد.")),
    "/professors": ([L("Open a professor's space.", "Ouvrez l'espace d'un professeur.", "افتح فضاء أستاذ."),
                     L("Add a speciality or an absence.", "Ajoutez une spécialité ou une absence.", "أضف تخصصا أو غيابا.")],
                    L("Their classes, absences and reviews; who teaches each program.", "Ses classes, absences et avis ; qui enseigne chaque formation.",
                      "أقسامه وغياباته وآراؤه؛ ومن يدرّس كل برنامج.")),
    "/quiz/all": ([L("Open a level check that was answered.", "Ouvrez un test de niveau répondu.", "افتح اختبار مستوى تمت الإجابة عنه."),
                   L("Look at the timing card.", "Regardez la carte du moment d'envoi.", "انظر إلى بطاقة توقيت الإرسال.")],
                  L("Each answer one by one, the level found and how they behaved.", "Chaque réponse une à une, le niveau trouvé et leur comportement.",
                    "كل إجابة على حدة، والمستوى المحدد، وسلوكه.")),
    "/programs/all": ([L("Open a program, then its PDF.", "Ouvrez une formation, puis son PDF.", "افتح برنامجا ثم ملفه PDF."),
                       L("Look at its recommendations card.", "Regardez sa carte de recommandations.", "انظر إلى بطاقة توصياته.")],
                      L("Its weeks, price and pictures; a clean PDF; what to add from the job market.", "Ses semaines, son prix et ses images ; un PDF propre ; quoi ajouter selon le marché.",
                        "أسابيعه وسعره وصوره؛ ملف PDF أنيق؛ وما يُضاف حسب سوق العمل.")),
    "/programs/expertise": ([L("Teach a fact to one program.", "Apprenez un fait à une formation.", "علّم معلومة لبرنامج."),
                             L("Ask Yasmine about it.", "Posez-en la question à Yasmine.", "اسأل ياسمين عنها.")],
                            L("Every assistant knows it at once.", "Chaque assistant le sait aussitôt.", "يعرفها كل مساعد فورا.")),
    "/programs/documents": ([L("Make “All programs in short” in French.", "Faites « Toutes les formations en bref » en français.", "أنشئ «كل البرامج باختصار» بالفرنسية.")],
                            L("A PDF of 2 or 3 pages with the welcome page.", "Un PDF de 2 ou 3 pages avec la page d'accueil.", "ملف PDF من صفحتين أو ثلاث مع صفحة الترحيب.")),
    "/programs/videos": ([L("Play one video.", "Lancez une vidéo.", "شغّل فيديو.")],
                         L("It plays inside the application.", "Elle se lit dans l'application.", "يُعرض داخل التطبيق.")),
    "/programs/market": ([L("Paste one job offer.", "Collez une offre d'emploi.", "الصق عرض عمل.")],
                         L("Its skills are counted and compared with your programs.", "Ses compétences sont comptées et comparées à vos formations.", "تُحسب مهاراته وتُقارن ببرامجك.")),
    "/inbox/compose": ([L("Write to your test candidate, with a square and a file.", "Écrivez à votre candidat de test, avec un carré et un fichier.", "اكتب لمترشح التجربة مع مربع وملف."),
                        L("Look at the preview, then send.", "Regardez l'aperçu, puis envoyez.", "انظر إلى المعاينة ثم أرسل.")],
                       L("The email arrives as previewed, with your signature.", "L'email arrive comme dans l'aperçu, avec votre signature.", "يصل البريد كما في المعاينة مع توقيعك.")),
    "/inbox/templates": ([L("Open the welcome message, change a word, then “Send me a test”.", "Ouvrez le message de bienvenue, changez un mot, puis « M'envoyer un test ».",
                            "افتح رسالة الترحيب، غيّر كلمة ثم «أرسل لي تجربة».")],
                         L("You receive it with your change; “Reset” brings back the original.", "Vous le recevez avec votre changement ; « Rétablir » remet l'original.",
                           "تصلك بتغييرك؛ و«استعادة» تعيد الأصل.")),
    "/inbox/knowledge": ([L("Write a fact, then test a question about it.", "Écrivez un fait, puis testez une question à son sujet.", "اكتب معلومة ثم اختبر سؤالا عنها.")],
                         L("The test finds it, with how sure it is.", "Le test la trouve, avec son degré de certitude.", "يجدها الاختبار مع درجة الثقة.")),
    "/inbox/team": ([L("Write to a professor in the application.", "Écrivez à un professeur dans l'application.", "اكتب لأستاذ داخل التطبيق.")],
                    L("It appears in their messages; their answer comes back here.", "Il apparaît dans ses messages ; sa réponse revient ici.", "يظهر في رسائله؛ ويعود رده إلى هنا.")),
    "/assistant/agents": ([L("Type a sentence in “Try a sentence”.", "Tapez une phrase dans « Essayer une phrase ».", "اكتب جملة في «جرّب جملة».")],
                          L("The step and the agent that would take it light up.", "L'étape et l'agent qui la prendraient s'allument.", "تضيء الخطوة والوكيل الذي سيتولاها.")),
    "/social": ([L("Make a post idea in Tounsi, then its picture.", "Faites une idée de post en tounsi, puis son image.", "أنشئ فكرة منشور بالتونسي ثم صورتها.")],
                L("A square picture ready to download.", "Une image carrée prête à télécharger.", "صورة مربعة جاهزة للتنزيل.")),
    "/website": ([L("Press “Read again now”.", "Appuyez sur « Relire maintenant ».", "اضغط «أعد القراءة الآن».")],
                 L("What changed on 9antra.tn is listed; your own words are never replaced.", "Ce qui a changé sur 9antra.tn est listé ; vos propres mots ne sont jamais remplacés.",
                   "يُعرض ما تغير في 9antra.tn؛ ولا تُستبدل كلماتك أبدا.")),
    "/journal": ([L("Open the journal and read one article.", "Ouvrez le journal et lisez un article.", "افتح الجريدة واقرأ مقالا.")],
                 L("News per field, with pictures, read every 6 hours.", "Des nouvelles par domaine, avec images, lues toutes les 6 heures.", "أخبار حسب المجال مع صور، تُقرأ كل 6 ساعات.")),
    "/settings": ([L("Change the colour or the day/night look.", "Changez la couleur ou l'apparence jour/nuit.", "غيّر اللون أو مظهر النهار/الليل.")],
                  L("Every open page, on every device, changes at once.", "Chaque page ouverte, sur chaque appareil, change aussitôt.", "تتغير كل صفحة مفتوحة على كل جهاز فورا.")),
    "/settings/team": ([L("Add a team member who may only look at Payments.", "Ajoutez un membre qui peut seulement regarder les paiements.", "أضف عضوا يمكنه فقط رؤية الدفعات."),
                        L("Use “See as” to look as them.", "Utilisez « Voir comme » pour regarder comme lui.", "استعمل «عرض كـ» لترى كما يرى.")],
                       L("They see Payments only, without buttons to change.", "Il ne voit que les paiements, sans boutons pour changer.", "لا يرى إلا الدفعات، دون أزرار تغيير.")),
    "/settings/translations": ([L("Switch the page to French, then to Arabic.", "Passez la page en français, puis en arabe.", "حوّل الصفحة إلى الفرنسية ثم العربية.")],
                               L("The pages are in that language; a correction you make is kept.", "Les pages sont dans cette langue ; une correction faite est gardée.",
                                 "تظهر الصفحات بتلك اللغة؛ ويُحفظ أي تصحيح تقوم به.")),
    "/system": ([L("Press “Start now” on one piece of work.", "Appuyez sur « Lancer maintenant » sur un travail.", "اضغط «ابدأ الآن» على عمل.")],
                L("It runs and says when it is done.", "Il tourne et dit quand c'est fait.", "يعمل ويقول متى انتهى.")),
    "/data": ([L("Open Candidates, change a phone number, then “Excel”.", "Ouvrez Candidats, changez un numéro, puis « Excel ».", "افتح المترشحين، غيّر رقم هاتف ثم «Excel».")],
              L("The change is saved and written in the edit log; the file opens in Excel.", "Le changement est gardé et noté dans le journal ; le fichier s'ouvre dans Excel.",
                "يُحفظ التغيير ويُسجل في سجل التعديلات؛ ويُفتح الملف في Excel.")),
}


# What the application does by itself: how to see it happen (the list itself is automations.py).
AUTO = {
    "welcome": L("Register your test candidate with the form: the welcome message arrives at once.", "Inscrivez votre candidat de test par le formulaire : le message de bienvenue arrive aussitôt.",
                 "سجّل مترشح التجربة بالاستمارة: تصل رسالة الترحيب فورا."),
    "payment": L("Leave your test candidate unpaid: one reminder arrives after the delay, never a second one by itself.",
                 "Laissez votre candidat de test non payé : un rappel arrive après le délai, jamais un deuxième tout seul.",
                 "اترك مترشح التجربة دون دفع: يصل تذكير واحد بعد المهلة، ولا يُرسل ثان وحده."),
    "proofs": L("Email a picture of a receipt from your test address: it appears in Payments › proofs, read.",
                "Envoyez la photo d'un reçu depuis votre adresse de test : elle apparaît dans Paiements › preuves, lue.",
                "أرسل صورة وصل من عنوان التجربة: تظهر في الدفعات › الإثباتات مقروءة."),
    "classes": L("Record a full payment: your test candidate is placed in a class, you get one notification.",
                 "Enregistrez un paiement complet : votre candidat de test est placé dans une classe, vous recevez une notification.",
                 "سجّل دفعة كاملة: يُوضع مترشح التجربة في قسم وتصلك إشعار واحد."),
    "level": L("Once paid, the level check arrives at the time set (Level checks › timing).", "Une fois payé, le test de niveau arrive au moment choisi (Tests de niveau › moment).",
               "بعد الدفع يصل اختبار المستوى في الوقت المحدد (اختبارات المستوى › التوقيت)."),
    "countdown": L("A class starting in 7, 3 or 1 days: its candidates get the countdown email that day.", "Une classe qui commence dans 7, 3 ou 1 jours : ses candidats reçoivent l'email ce jour-là.",
                   "قسم يبدأ بعد 7 أو 3 أو يوم واحد: يصل مترشحيه بريد العد التنازلي ذلك اليوم."),
    "session_reminder": L("The day before a session of your test class, the reminder arrives.", "La veille d'une séance de votre classe de test, le rappel arrive.",
                          "قبل يوم من حصة قسم التجربة يصل التذكير."),
    "links": L("An online session: within the hour before, each candidate gets their own link.", "Une séance en ligne : dans l'heure avant, chaque candidat reçoit son lien.",
               "حصة عن بعد: قبلها بساعة يصل كل مترشح رابطه الخاص."),
    "weekly": L("A class set to automatic: after its week, the questions arrive; answer them by email “1-B, 2-A”.",
                "Une classe en automatique : après sa semaine, les questions arrivent ; répondez par email « 1-B, 2-A ».",
                "قسم على الوضع التلقائي: بعد أسبوعه تصل الأسئلة؛ أجب بالبريد «1-B, 2-A»."),
    "session": L("After a session, its questions arrive at the hours set; the answers show on the presence sheet.",
                 "Après une séance, ses questions arrivent au délai choisi ; les réponses s'affichent sur la feuille de présence.",
                 "بعد الحصة تصل أسئلتها في المهلة المحددة؛ وتظهر الإجابات في ورقة الحضور."),
    "resources": L("A week without a file: its professor is reminded before it starts.", "Une semaine sans fichier : son professeur est rappelé avant qu'elle commence.",
                   "أسبوع دون ملف: يُذكَّر أستاذه قبل بدايته."),
    "questions": L("From your test address, ask a course question: it goes to the professor, the answer comes back to you.",
                   "Depuis votre adresse de test, posez une question de cours : elle va au professeur, la réponse vous revient.",
                   "من عنوان التجربة اطرح سؤالا عن الدرس: يذهب إلى الأستاذ ويعود الرد إليك."),
    "reviews": L("When your test class finishes, the review request arrives with five stars to tap.", "Quand votre classe de test finit, la demande d'avis arrive avec cinq étoiles à toucher.",
                 "عند انتهاء قسم التجربة يصل طلب الرأي مع خمس نجوم للنقر."),
    "answers": L("Email the centre from your test address: it is answered alone when sure, or waits in the Inbox.",
                 "Écrivez au centre depuis votre adresse de test : réponse seule si sûre, sinon attente dans la boîte de réception.",
                 "راسل المركز من عنوان التجربة: يُجاب وحده إن كان واثقا وإلا ينتظر في صندوق الوارد."),
    "whatsapp": L("Needs the WhatsApp Business number: then write to it from your phone.", "Il faut le numéro WhatsApp Business : ensuite écrivez-lui depuis votre téléphone.",
                  "يتطلب رقم واتساب للأعمال: ثم راسله من هاتفك."),
    "sms": L("Needs the Android phone: then send it an SMS.", "Il faut le téléphone Android : ensuite envoyez-lui un SMS.", "يتطلب هاتف أندرويد: ثم أرسل له رسالة SMS."),
    "ways": L("Remove your test candidate's email, then remind them: it goes by WhatsApp or SMS instead.",
              "Enlevez l'email de votre candidat de test, puis rappelez-le : cela part par WhatsApp ou SMS.",
              "احذف بريد مترشح التجربة ثم ذكّره: يُرسل عبر واتساب أو SMS."),
    "recommendations": L("Open a program: its recommendations card says what was read and when.", "Ouvrez une formation : sa carte de recommandations dit ce qui a été lu et quand.",
                         "افتح برنامجا: تقول بطاقة التوصيات ما قُرئ ومتى."),
    "website": L("On Website, the date of the last reading is less than 6 hours old.", "Sur Site web, la date de la dernière lecture a moins de 6 heures.",
                 "في الموقع، تاريخ آخر قراءة أقل من 6 ساعات."),
    "report": L("Put your own address in Settings › report, every 1 day: it arrives the next day.", "Mettez votre adresse dans Réglages › rapport, tous les 1 jour : il arrive le lendemain.",
                "ضع عنوانك في الإعدادات › التقرير، كل يوم: يصل في اليوم التالي."),
    "backup": L("On System › safety, the last copy is from today and “checked”.", "Dans Système › sécurité, la dernière copie est d'aujourd'hui et « vérifiée ».",
                "في النظام › الأمان، آخر نسخة من اليوم و«متحقق منها»."),
    "friend": L("The first page of the day: Yasmine says hi with what waits for you.", "La première page du jour : Yasmine dit bonjour avec ce qui vous attend.",
                "أول صفحة في اليوم: تحييك ياسمين بما ينتظرك."),
}


# ------------------------------------------------------------------
# Marks
# ------------------------------------------------------------------

def marks(db: Session) -> dict[str, dict]:
    from app.models.test_mark import TestMark

    from app.services.schedule_utils import utc_to_local

    def when(moment):
        local = utc_to_local(moment.replace(tzinfo=None)) if moment is not None else None
        return local.strftime("%d/%m/%Y %H:%M") if local is not None else ""

    return {row.key: {"status": row.status, "note": row.note or "", "by": row.by or "", "at": when(row.at)} for row in db.query(TestMark).all()}


def mark(db: Session, key: str, status: str, note: str = "", by: str = "") -> dict:
    """It works (ok), a problem (with what), or not tested again (clear)."""
    from app.models.test_mark import TestMark

    key = (key or "").strip()[:200]
    if not key or status not in ("ok", "problem", "clear"):
        raise ValueError("Unknown test or result.")
    row = db.query(TestMark).filter(TestMark.key == key).first()
    if status == "clear":
        if row is not None:
            db.delete(row)
        db.commit()
        return {"key": key, "status": None}
    if row is None:
        row = TestMark(key=key, status=status)
        db.add(row)
    row.status, row.note, row.by, row.at = status, " ".join((note or "").split())[:600] or None, by[:80], datetime.utcnow()
    db.commit()
    return {"key": key, "status": status, "note": row.note or ""}


def book(db: Session, user=None) -> dict:
    """Everything to test, in the page's language, each with its mark: news, pages, by itself, Yasmine; and the count."""
    from app.services import abilities, automations, guides

    found = marks(db)

    def test(key, title, url, steps, expect, sends=False, text=""):
        return {"key": key, "title": title, "url": url, "steps": steps, "expect": expect, "sends": sends, "text": text, "mark": found.get(key)}

    news = [{"date": item["date"], "title": say(item["title"]),
             "tests": [test(one["key"], say(one["title"]), one["url"], [say(step) for step in one["steps"]], say(one["expect"]), one["sends"])
                       for one in item["items"]]} for item in NEWS]
    pages = []
    for group in guides.everything(user):
        rows = []
        for guide in group["pages"]:
            steps, expect = PAGES.get(guide["url"], ([], None))
            if expect is None:
                continue
            rows.append(test(f"page:{guide['url']}", ui_words.t(guide["title"]), guide["url"], [say(step) for step in steps], say(expect),
                             text=ui_words.t(guide["text"])))
        if rows:
            pages.append({"title": ui_words.t(group["title"]), "tests": rows})
    if not any(row["url"] == "/professors" for group in pages for row in group["tests"]):
        steps, expect = PAGES["/professors"]
        pages[0]["tests"].append(test("page:/professors", ui_words.t("Professors"), "/professors", [say(step) for step in steps], say(expect)))
    alone = [test(f"auto:{item['key']}", ui_words.t(item["title"]), item["change"], [say(AUTO[item["key"]])], ui_words.t(item["what"]),
                  sends=item["key"] not in ("recommendations", "website", "backup", "friend"), text=ui_words.t(item["when"]))
             for item in automations.listing(db) if item["key"] in AUTO]
    lang = ui_words.language()
    yasmine = [test(f"yasmine:{card['key']}", card["title"], None, [card["ask"]], card["text"])
               for card in (abilities._card(item, lang if lang in ("en", "fr", "ar") else "en") for item in abilities.ABILITIES
                            if abilities._for(user, item[4]))]
    every = [row for item in news for row in item["tests"]] + [row for group in pages for row in group["tests"]] + alone + yasmine
    keys = {row["key"] for row in every}
    done = [row for row in every if row["mark"]]
    problems = [row for row in every if row["mark"] and row["mark"]["status"] == "problem"]
    return {"news": news, "pages": pages, "alone": alone, "yasmine": yasmine, "problems": problems,
            "progress": {"total": len(keys), "done": len(done), "ok": len(done) - len(problems), "problems": len(problems),
                         "percent": round(100 * len(done) / len(keys)) if keys else 0}}
