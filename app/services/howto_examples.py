"""How people ask for each task of the guide, in English, French, Arabic and Tunisian: the guide finds the task whose
examples are closest in meaning to a question its word rules did not recognise (services/meaning.py). Written by hand;
the titles of the tasks in the four languages are added to them."""

EXAMPLES = {
    "professor-absence": [
        "A professor cannot come next week", "The trainer is off on Saturday", "Le formateur sera absent mardi",
        "Notre prof ne peut pas assurer la séance", "المكوّن غائب يوم السبت", "el formateur mouch bech yji samedi",
    ],
    "professor-access": [
        "How does a professor sign in?", "Give the trainer a login and a password", "Créer un compte pour un formateur",
        "Le prof veut voir ses classes sur son téléphone", "كيف يدخل الأستاذ إلى التطبيق؟", "nheb na3ti el prof compte bech yodkhel",
    ],
    "translations-fix": [
        "This French text is wrong", "The Arabic of a button is not right", "Une traduction est fausse sur la page",
        "Corriger un mot mal traduit", "الترجمة العربية خاطئة", "el traduction ghalta",
    ],
    "duplicates": [
        "The same program appears twice", "Merge two programs that are the same", "J'ai deux fois la même formation",
        "Fusionner des formations en double", "البرنامج مكرّر مرتين", "famma formation m3awda zouz marrat",
    ],
    "level-results": [
        "What level is this candidate at?", "See the answers of the placement test", "Voir le test de niveau d'un étudiant",
        "Où sont les résultats du test d'entrée ?", "كيف أرى نتيجة اختبار المستوى؟", "win nchouf natija test niveau",
    ],
    "email-write": [
        "Send an email to the whole class", "Write to every candidate at once", "Envoyer un mail à tous les inscrits",
        "Écrire un message à un groupe", "كيف أرسل بريدا لكل المترشحين؟", "nheb neb3ath mail lel candidats lkol",
    ],
    "candidate-add": [
        "Someone wants to join, where do I enter them?", "Register a new student by hand", "Ajouter un nouvel inscrit",
        "Saisir un candidat qui a appelé", "كيف أضيف مترشحا جديدا؟", "kifech nzid candidat jdid",
    ],
    "payment-record": [
        "A candidate paid in cash today", "Mark a registration as paid", "Saisir un versement d'un étudiant",
        "Il a donné l'argent de la formation", "المترشح دفع المبلغ، أين أسجله؟", "el candidat 3tani el flous",
    ],
    "class-move": [
        "Put a student in a different group", "Transfer a candidate to the evening class", "Changer un étudiant de classe",
        "Faire passer un candidat dans un autre groupe", "نقل مترشح من قسم إلى آخر", "nheb n7awel el candidat l classe okhra",
    ],
    "class-time": [
        "Change the hours of a class", "Move the sessions to Sunday", "Modifier l'horaire d'une classe",
        "Les séances doivent commencer à 18h", "تغيير توقيت القسم", "nbadel el wa9t mta3 el séances",
    ],
    "session-link": [
        "Where do I put the Google Meet link?", "Online session link for the candidates", "Mettre le lien de la visio",
        "Ajouter le lien Teams de la séance", "رابط الحصة عن بعد", "win n7ot lien el meet",
    ],
    "program-pdf": [
        "Email the program document to a student", "Download the brochure of a program", "Envoyer le programme en PDF",
        "Partager la fiche de la formation avec un candidat", "إرسال البرنامج بصيغة PDF", "neb3ath el programme pdf l candidat",
    ],
    "program-edit": [
        "Change the price of a program", "The number of weeks of a course changed", "Modifier le tarif d'une formation",
        "Mettre à jour les heures d'un programme", "تعديل سعر البرنامج", "nbadel el prix mta3 el formation",
    ],
    "reminder-plan": [
        "Remind the class to bring their laptops", "Schedule a message for Friday morning", "Programmer un rappel pour un groupe",
        "Envoyer un rappel plus tard", "برمجة تذكير للمترشحين", "nheb nfakker el classe nhar jom3a",
    ],
    "weekly-questions": [
        "Send a quiz after the lesson", "Questions to check what candidates understood this week", "Envoyer les questions de la semaine",
        "Un petit test sur le cours de la semaine", "أسئلة الأسبوع للمترشحين", "neb3ath les questions mta3 el jem3a",
    ],
    "email-setup": [
        "Connect my Gmail account", "Emails are not being sent, set up the mail", "Configurer l'adresse email du centre",
        "Le mot de passe d'application Gmail", "ربط البريد الإلكتروني", "kifech nrabet el gmail",
    ],
    "whatsapp-setup": [
        "Connect WhatsApp Business", "Send messages on WhatsApp automatically", "Configurer WhatsApp Business",
        "Brancher notre numéro WhatsApp", "ربط واتساب بيزنس", "nheb nrabet whatsapp",
    ],
    "sms-setup": [
        "Send text messages from the app", "Use my Android phone for SMS", "Envoyer des SMS aux candidats",
        "Configurer les SMS", "إرسال رسائل قصيرة", "kifech neb3ath sms",
    ],
    "ways": [
        "Should messages go by email or WhatsApp?", "Choose the channel for messages", "Par quel moyen partent les messages ?",
        "Choisir email ou WhatsApp pour les candidats", "اختيار طريقة إرسال الرسائل", "el messages yemchiw b email wala whatsapp",
    ],
    "team-add": [
        "Add a colleague to the application", "Give a secretary an account", "Ajouter un membre à l'équipe",
        "Donner accès à une assistante", "إضافة عضو إلى الفريق", "nzid wa7ed m3ana fel équipe",
    ],
    "view-as": [
        "See what a professor sees", "Look at the app as my colleague", "Voir l'application comme un formateur",
        "Vérifier ce que voit un membre de l'équipe", "رؤية التطبيق كما يراه المكوّن", "nheb nchouf kifech yra el prof",
    ],
    "professor-delete": [
        "Remove a teacher who left us", "Delete this professor from the list", "Supprimer un formateur",
        "Le prof ne travaille plus avec nous, l'enlever", "حذف مكوّن من القائمة", "nheb nfasa5 el prof",
    ],
    "professor-add": [
        "Add a new teacher", "A trainer joins us for Python", "Ajouter un formateur",
        "Nouveau prof pour une formation", "إضافة أستاذ جديد", "nzid prof jdid",
    ],
    "resources-remind": [
        "Remind teachers to upload their slides", "Professors forget to add their course files", "Rappeler aux formateurs d'ajouter leurs supports",
        "Relancer les profs pour les ressources", "تذكير المكوّنين بإضافة الموارد", "fakker el profs y7ottou les supports",
    ],
    "resources-add": [
        "Upload my slides for week three", "Where do I put the course files?", "Ajouter mes supports de cours",
        "Déposer les exercices de la semaine", "أين أضع ملفات الدرس؟", "win n7ot les fichiers mta3 el cours",
    ],
    "presence": [
        "Take attendance", "Who was absent today?", "Faire l'appel de la séance",
        "Noter les absents", "تسجيل الحضور", "nheb n3ayet el présence",
    ],
    "answer-candidate": [
        "Reply to a student's question", "A candidate is waiting for an answer", "Répondre à un message d'un candidat",
        "Un étudiant m'a écrit", "الرد على رسالة مترشح", "nraja3 jaweb l candidat",
    ],
    "send-message": [
        "Ask the assistant to text someone", "Let the chatbot send a WhatsApp for me", "Demander à l'assistant d'envoyer un message",
        "Faire écrire l'assistant à un candidat", "اطلب من المساعد إرسال رسالة", "9oul lel assistant yeb3ath message",
    ],
    "team-chat": [
        "Message a professor", "Chat with my team", "Écrire à un formateur",
        "Discuter avec un collègue", "مراسلة المكوّن", "nheb nahki m3a el prof",
    ],
    "proofs": [
        "A candidate sent a photo of the bank transfer", "Check a payment receipt", "Vérifier un reçu de virement",
        "Confirmer une preuve de paiement", "التحقق من وصل الدفع", "candidat b3ath taswira mta3 el virement",
    ],
    "rib": [
        "Where do I write our bank account?", "Add the centre's IBAN", "Écrire le RIB du centre",
        "Le numéro de compte bancaire", "إضافة الحساب البنكي للمركز", "win nekteb el rib",
    ],
    "welcome": [
        "Change the first email candidates receive", "Edit the welcome text", "Modifier le message de bienvenue",
        "Le mail envoyé après l'inscription", "تغيير رسالة الترحيب", "nbadel message el bienvenue",
    ],
    "payment-reminder": [
        "Change the text of the payment reminder", "Edit the message about unpaid fees", "Modifier le rappel de paiement",
        "Le mail pour ceux qui n'ont pas payé", "تعديل تذكير الدفع", "nbadel el rappel mta3 el khlas",
    ],
    "level-timing": [
        "When is the placement test sent?", "Send the level test a week before", "Quand part le test de niveau ?",
        "Changer la date d'envoi du test", "متى يُرسل اختبار المستوى؟", "waqtech yemchi el test",
    ],
    "reviews": [
        "What did candidates think of the program?", "Ask the class for their opinion", "Voir les avis des candidats",
        "Demander une note à la fin de la formation", "آراء المترشحين", "chnowa 9alou el candidats 3al formation",
    ],
    "recommendations": [
        "What should we add to the program?", "Which skills are missing from the course?", "Que faut-il ajouter au programme ?",
        "Améliorer le contenu d'une formation", "ماذا نضيف للبرنامج؟", "chnowa nzidou fel programme",
    ],
    "catalogue": [
        "Import all programs from the PDF", "Upload the planning document", "Importer le catalogue des formations",
        "Charger le fichier des cours", "استيراد كتالوج البرامج", "nheb n7ammel el catalogue",
    ],
    "videos": [
        "Watch the course recordings", "Where are the videos?", "Regarder les vidéos des cours",
        "Les enregistrements des séances", "أين الفيديوهات؟", "win el videos",
    ],
    "phone": [
        "Open the app on my mobile", "Install it on my iPhone", "Utiliser l'application sur le téléphone",
        "Ouvrir sur mon portable", "استعمال التطبيق على الهاتف", "nheb n7allha 3al portable",
    ],
    "automatic": [
        "What does the system do on its own?", "Which tasks are automatic?", "Qu'est-ce qui est automatique ?",
        "Ce que l'application fait sans moi", "ماذا يفعل التطبيق تلقائيا؟", "chnowa ya3mel el système wa7dou",
    ],
    "language": [
        "Show the pages in Arabic", "Switch the interface to French", "Changer la langue de l'application",
        "Mettre les pages en anglais", "تغيير لغة الصفحات", "nbadel el logha",
    ],
    "password": [
        "I want a new password", "Change my login password", "Modifier mon mot de passe",
        "J'ai oublié mon mot de passe", "تغيير كلمة المرور", "nbadel el mot de passe",
    ],
    "class-create": [
        "Start a new group for a program", "Open another session of the course", "Créer une nouvelle classe",
        "Lancer un groupe pour les inscrits", "إنشاء قسم جديد", "na3mel groupe jdid",
    ],
    "program-add": [
        "Add a new course to the catalogue", "We have a new training to offer", "Ajouter une nouvelle formation",
        "Créer un programme", "إضافة برنامج تكوين جديد", "nzid formation jdida",
    ],
    "analysis": [
        "How is the centre doing?", "Show me the progress report", "Voir l'analyse et le rapport",
        "Est-ce que les candidats progressent ?", "تقرير التقدّم", "kifech mechya el affaire",
    ],
    "export": [
        "Download the list of candidates", "Get the payments in a spreadsheet", "Exporter les données vers Excel",
        "Télécharger la liste en CSV", "تصدير القائمة إلى إكسل", "nheb el liste fi excel",
    ],
    "edit-data": [
        "Fix a wrong value in the database", "Change a field that has no form", "Modifier une donnée directement",
        "Corriger une information enregistrée", "تعديل أي قيمة في البيانات", "nbadel donnée ghalta",
    ],
    "backup": [
        "Make a copy of the database", "Restore yesterday's data", "Sauvegarder les données",
        "Récupérer une sauvegarde", "نسخة احتياطية من البيانات", "na3mel copie mta3 les données",
    ],
    "design": [
        "Change the colours of the app", "Put the menu on the right", "Passer en mode nuit",
        "Changer l'apparence", "تغيير ألوان التطبيق", "nbadel el design",
    ],
    "find": [
        "Search for a candidate by name", "Look something up quickly", "Rechercher une personne",
        "Trouver une page rapidement", "البحث عن مترشح", "nlawej 3la candidat",
    ],
    "knowledge-teach": [
        "Teach the assistant a new answer", "The bot gave a wrong answer, how do I correct it?", "Apprendre une réponse à l'assistant",
        "Ajouter une information pour le chatbot", "تعليم المساعد إجابة", "n3allem el assistant",
    ],
    "assistant-rules": [
        "The assistant must never talk about refunds", "Stop the bot answering some questions", "Interdire à l'assistant certains sujets",
        "Règles de l'assistant", "قواعد المساعد", "el assistant ma yjawebch 3la...",
    ],
    "facebook": [
        "Connect our Facebook page", "Read the posts and reviews of the page", "Relier la page Facebook",
        "Les avis Facebook dans l'application", "ربط صفحة فيسبوك", "nrabet el page facebook",
    ],
    "website": [
        "Get the latest changes from the website", "Read 9antra.tn again", "Mettre à jour depuis le site",
        "Le site a de nouvelles formations", "تحديث المعلومات من الموقع", "el site tbaddel",
    ],
    "notifications": [
        "What are the alerts at the top?", "Mark all notifications as read", "Les notifications, comment ça marche ?",
        "Que veut dire le chiffre sur la cloche ?", "الإشعارات", "chnowa el notifications",
    ],
}
