"""The guide's steps in Tunisian, as people type it (Latin letters, 3 = ع, 7 = ح, 9 = ق, 5 = خ), with the application's
own words in French as they appear on the pages. Written by hand; a task missing here is answered in French."""

TN: dict[str, tuple[str, list[str]]] = {
    "email-setup": ("Arbot el Gmail mte3ek bech el application tab3ath w ta9ra el emails", [
        "Fi compte Google mte3ek, 7el « Validation en 2 étapes » (Sécurité).",
        "A3mel « mot de passe d'application » (Sécurité › Mots de passe des applications): 16 7arf.",
        "Paramètres › Envoi des emails: ekteb el adresse Gmail w el mot de passe hedheka, ba3d Enregistrer: yet7a9a9 m3a Gmail fissa3.",
        "Ba3d, el boîte mail tet9ra kol d9i9a w les emails mta3 el candidats youslou l Boîte de réception."]),
    "whatsapp-setup": ("Arbot numéro WhatsApp Business", [
        "Fi Meta for Developers, a3mel application type Business w zid WhatsApp.",
        "WhatsApp › API Setup: zid el numéro mte3ek w copi el Phone number ID.",
        "Paramètres de l'entreprise › Utilisateurs système: a3mel wa7ed, a3tih el application w a3mel token permanent.",
        "App settings › Basic: copi el App secret.",
        "Paramètres › WhatsApp Business: ekteb el 4 valeurs, Enregistrer, ba3d « Tester la connexion ».",
        "Webhook: 7ot el adresse w el jeton de vérification elli fil carte, w abonni l « messages »."]),
    "sms-setup": ("Ab3ath w e9bel SMS men téléphone Android", [
        "Fi téléphone Android fih puce, installi l'application « SMS Gateway for Android » (sms-gate.app), blech.",
        "Fil application, 7ell Local server: ekteb el adresse, el username w el mot de passe.",
        "Fi Settings › Webhooks mte3ha, copi el clé de signature.",
        "Paramètres › SMS depuis votre téléphone Android: ekteb el adresse, username, mot de passe w el clé, Enregistrer, ba3d « Tester le téléphone ».",
        "Zid webhook l sms:received 3al adresse elli fil carte.",
        "Ba3d, les SMS mta3 el candidats youslou l Boîte de réception (filtre « SMS ») w yet3amlou kif WhatsApp."]),
    "ways": ("Ikhtar kifech temchi el messages (email, WhatsApp, SMS)", [
        "7ell el Boîte de réception.", "El fou9: « Les messages passent par »: Laisser le système choisir, Email, WhatsApp wala SMS.",
        "7ott 3lama 3la « Si ça ne marche pas, le moyen suivant » bech ken wa7da ma mchetech, ijarreb elli ba3dha."]),
    "team-add": ("Zid 7add lel équipe w ikhtar chnowa ynajem ya3mel", [
        "Paramètres › Équipe et accès › Ajouter une personne.", "Ikhtar: membre de l'équipe, administrateur, wala compte mta3 formateur.",
        "Lel membre: kol partie « Non affiché / Peut regarder / Peut modifier », droits spéciaux, wala « Ajouter avec accès à tout ».",
        "A3tih enti el mot de passe louwel."]),
    "view-as": ("Chouf l'application kif ma ychoufha formateur wala membre de l'équipe", [
        "Paramètres › Équipe et accès.", "3al carte mte3ou: « Voir l'application comme … ».",
        "Tchouf el menu w les pages mte3ou bidhabt; ma tnajem tbadel chay.",
        "El barre safra el fou9: « Changer ce qu'il peut faire », wala « Retour à ma vue »."]),
    "professor-add": ("Zid formateur l formation", [
        "Classes › Formateurs › « Qui enseigne chaque formation ».",
        "« L'enseigne aussi » l 7add ya5dem m3ak deja, wala « Un nouveau formateur » (esm, email, téléphone): 9addech ma t7eb.",
        "El formations elli 7atta 7add ma y9arrihom yjiw louwel, bel a7mar: ma tet7allech classe 7atta ykoun 3andha formateur."]),
    "resources-remind": ("Fakker les formateurs bech yzidou les ressources mte3hom", [
        "Classes › Formateurs › « Rappeler aux formateurs d'ajouter leurs ressources ».",
        "Ikhtar 9adech men nhar 9bal el jem3a, w ekteb el klem mte3ek.",
        "« Enregistrer et envoyer ceux qui sont dus maintenant » yab3ath elli lezem lyoum."]),
    "resources-add": ("Zid les ressources mta3 jem3a (formateurs)", [
        "Mes classes › 7ell el classe › Cours.", "Ikhtar el jem3a, ba3d zid les fichiers: slides, PDF, vidéo, tsawer, m3a note.",
        "El candidats yal9awhom fi jem3athom; w el assistant yet3allem menhom zeda."]),
    "presence": ("Sajjel chkoun 7dher (formateurs)", [
        "Mes classes › 7ell el classe › Présence.", "Lkol candidat: présent, en retard wala absent; zid kifech chérék w les questions elli 7attha.",
        "Enregistrer: el dossier mta3 el candidat w el dynamique mta3 el classe yetbaddlou."]),
    "answer-candidate": ("Jaweb candidat", [
        "Boîte de réception › el discussion › ekteb fil case mta3 el réponse (chwaya klem yekfiw, 7atta bel tounsi).",
        "Ikhtar « en français / en anglais » w enzel 3la Reformuler, wala 7ott 3lama 3la « Reformuler et envoyer tout de suite ».",
        "Les formateurs: Messages › el question mta3 el candidat › Répondre au candidat."]),
    "send-message": ("Ab3ath message bel assistant", [
        "7ell el assistant (el bouton el mdawwer).", "9ollou « whatsapp » wala « sms » bech takhtar el wasila; « tel quel » ykhalli klemek kif ma howa.",
        "Y9ollek lkol 7add chnowa mcha, w 3lech la.", "« Envoyer tout de suite » fil fou9 mte3ou y7ell wala ysakker el envoi wa7dou."]),
    "team-chat": ("Ekteb l formateur wala lel équipe", [
        "Boîte de réception › Équipe (wala Discussion d'équipe fil menu).",
        "Ikhtar el 7add, ekteb marra, w ikhtar el wasa2el: fil application, email, WhatsApp, SMS.", "Ejwebthom yerja3ou fi nafs el discussion."]),
    "proofs": ("Rakka7 w akked preuve de paiement", [
        "Paiements › « Preuves de paiement à vérifier ».", "Chouf el taswira w kol vérification (montant, RIB, banque, date, tb3ath marrtin).",
        "Confirmer (ikhtar el inscription w el montant) bech yetsajjel el khlas, wala Refuser."]),
    "rib": ("Ekteb el RIB mta3 el centre", [
        "Paramètres › carte WhatsApp Business › Les numéros du centre.",
        "Ekteb el RIB (20 ra9m): el clé tet7a9a9; les preuves de paiement yet9arnou bih."]),
    "welcome": ("Badel el message de bienvenue", [
        "Boîte de réception › Modèles d'email › « Bienvenue, vient de s'inscrire ».", "Badel el klem; yemchi marra, ki ysajjel 7add."]),
    "payment-reminder": ("Badel el rappel de paiement", [
        "Boîte de réception › Modèles d'email › Rappel de paiement.", "Badel el klem w « Part … heures après l'inscription »."]),
    "level-timing": ("Ikhtar wa9tech yemchi el test de niveau", [
        "Tests de niveau › « Il part … jours avant la première séance » (wala ki ykhallsou).", "Rappel wa7ed yemchi ken ma jawbouch."]),
    "reviews": ("Chouf w otlob les avis", [
        "Yet3adda el sou2al wa7dou ki tkammel classe.",
        "Page mta3 el formation › « Ce que les apprenants ont dit »; dossier el candidat › Leurs avis › Demander son avis."]),
    "recommendations": ("E9ra les recommandations mta3 formation", [
        "Formations › 7ell formation › Recommandations (yetjaddou kol 24 se3a).",
        "Kol star fih les chiffres w les sources mte3ou; « Relire maintenant » yjaddedhom."]),
    "catalogue": ("Importi el catalogue mta3 les formations", [
        "Formations › Importer un PDF.", "Ikhtar el catalogue (Planning Courses): kol formation tet9ra houni, b les prix, les heures w les semaines.",
        "Rakka7 kol wa7da, ba3d Enregistrer."]),
    "videos": ("Chouf les vidéos mta3 les formations", [
        "Formations › Vidéos: el vidéo mta3 kol jem3a, tet9ra houni."]),
    "phone": ("Esta3mel l'application 3al téléphone", [
        "État du système › Sur votre téléphone: chaghelha lel téléphone, ba3d 7ell el adresse 3la nafs el Wi-Fi.",
        "iPhone: Safari › Partager › Sur l'écran d'accueil. Android: menu Chrome › Ajouter à l'écran d'accueil."]),
    "automatic": ("Chouf kol chay ta3mlou l'application wa7adha", [
        "État du système › « Tout ce qu'il fait pour vous, tout seul »: kol wa7da, wa9tech, mcha3la wala la, w win tbadelha."]),
    "language": ("Badel el logha mta3 les pages", [
        "El bouton mta3 el logha el fou9 (فرنسي، عربي، English): el ikhtiyar yo93ed fil navigateur mte3ek."]),
    "password": ("Badel el mot de passe mte3ek", [
        "Votre compte (Mot de passe) › ekteb el 9dim, ba3d el jdid marrtin."]),
    "candidate-add": ("Zid candidat", [
        "Candidats › « Ajouter un candidat » (wala Ctrl+K, « ajouter un candidat »).",
        "Ekteb el esm, el email w el téléphone (+216 tetzed wa7adha), w el formation ken ta3refha.",
        "Enregistrer: message de bienvenue yemchi fissa3, w rappel de paiement ba3d 24 se3a ken ma khallasch."]),
    "payment-record": ("Sajjel khlas", [
        "Candidats › 7ell el candidat › Formations et paiements.",
        "3al formation: « Enregistrer le paiement complet », wala ekteb el montant elli khallsou.",
        "Ki ykhallas kol chay: email de confirmation yemchi w el candidat yet7at fi classe mta3 el formation."]),
    "class-create": ("A3mel classe", [
        "Ghaleban ma 3andek chay: el classe tet3mal wa7adha ki candidat ykhallas w ma famech classe mta3 el formation mte3ou ma7loula. Bel yed, ken ki famma candidats khallsou w yestannaw.",
        "Classes › « Créer une classe ».",
        "Ikhtar el formation, ba3d formateur mel spécialité mte3ha (wala khalli el système yikhtar), el date, les jours w les heures.",
        "Créer: el candidats elli khallsou w yestannaw el formation hedhi yet7attou fiha."]),
    "class-time": ("7added les jours w les heures mta3 classe", [
        "Classes › 7ell el classe › « Paramètres de la classe ».",
        "Ikhtar les jours, se3et el bdeya w el nhaya, w el salle wala el lien mta3 el réunion.",
        "Enregistrer: les séances yetba3ou, w el candidats yjihom email ki tetbaddel el date wala el wa9t."]),
    "class-move": ("Na9el candidat l classe okhra", [
        "Classes › 7ell el classe › Tous les inscrits.", "Ikhtar el candidat › « Changer de classe » › ikhtar el classe › Déplacer.",
        "El candidat yjih email bel classe el jdida, el date w el wa9t."]),
    "program-add": ("Zid formation", [
        "Formations › « Ajouter une formation »: el esm barka yekfi.",
        "El assistant y9tara7 el description, les heures w el plan mta3 les semaines; taswira tetlawwej wa7adha.",
        "E9ra el proposition 3la carte el formation w a9balha: 7atta chay ma yetsajjel 9balha."]),
    "program-edit": ("Badel el soum, les heures wala les semaines mta3 formation", [
        "Formations › 7ell el formation.",
        "« Toutes les valeurs de cette formation » tbadel el soum, les heures, el description; el plan yetbaddel jem3a b jem3a.",
        "Enregistrer: les PDF, les emails w el assistant yesta3mlou el valeurs el jdod fissa3."]),
    "program-pdf": ("Ab3ath formation en PDF", [
        "Formations › 7ell el formation › PDF (wala page Documents PDF lkol chay).",
        "Ikhtar wa7ed mel 3 styles w el logha, ba3d « L'envoyer à un candidat ». Fi page mta3 candidat, « Envoyer le programme (PDF) » ya3melha b click wa7da.",
        "Wala 9oul lel assistant: « ab3ath el programme mta3 DevOps l Fares »; ken « Envoyer tout de suite » mcha3la, yemchi bel PDF."]),
    "email-write": ("Ekteb email l candidat, l classe wala lel kol", [
        "Boîte de réception › Écrire (wala « Écrire un email » fil tableau de bord).",
        "Ikhtar chkoun ye9blou: candidat, classe, formation, elli 3lihom flous, wala el kol.",
        "Ektbou, wala ebda men modèle, wala khalli el assistant yekteb brouillon; chouf l'aperçu, ba3d Envoyer."]),
    "reminder-plan": ("A3mel rappel mte3ek", [
        "Rappels › ikhtar chkoun: candidat, classe, formation, elli 3lihom flous, wala el kol.",
        "Ektbou, wala ebda men idée; zid carrés colorés lel 7ajet elli lezem tetchef.",
        "Ikhtar el nhar w el se3a: yemchi marra, wa7dou."]),
    "level-results": ("E9ra el test de niveau mta3 candidat", [
        "Tests de niveau › 7ell el candidat: ejwebtou wa7da wa7da, w el niveau mte3ou thème par thème.",
        "Page mta3 el classe tchouf fiha chnowa ya3ref el groupe el kol."]),
    "weekly-questions": ("Ab3ath les questions mta3 el jem3a", [
        "Classes › 7ell el classe › Cours › el jem3a.",
        "Zid les fichiers mta3 el jem3a wala chwaya notes, ba3d « Écrire les questions ».",
        "Rakka7hom, ba3d « Envoyer la semaine … »; wala khalli el classe tab3athhom wa7adha ba3d kol jem3a."]),
    "analysis": ("E9ra l'analyse w el rapport d'avancement", [
        "Tableau de bord › « Analyse : quoi améliorer » w « Progression des candidats »: kol wa7ed yet7al PDF.",
        "Paramètres › Rapport par email yab3athhomlek kol jem3a, kol 10 ayyem wala kif t7eb."]),
    "export": ("Khraj liste l Excel", [
        "Toutes les données › ikhtar el type (candidats, paiements, classes…).", "Enzel 3la « Excel »: el fichier yet7al fi Excel b kol chay."]),
    "edit-data": ("Badel ay valeur", [
        "Toutes les données › ikhtar el type › enzel 3al ligne.", "Badel ay valeur w Enregistrer: chkoun badel chnowa, w chnowa ken 9bal, yo93ed fil historique."]),
    "backup": ("A3mel wala raja3 copie mta3 les données", [
        "État du système › Vos données sont en sécurité › « Copier maintenant » (copie tet3mel zeda kol nhar).",
        "Zid dossier theni 3la disque e5er lel copies.",
        "Bech traja3 wa7da: wa99ef l'application, 7ott el app.db w el dossier data mta3 el copie fi blasethom, ba3d chaghelha."]),
    "design": ("Badel les couleurs, jiha mta3 el menu, nhar wala lil", [
        "Paramètres › Couleurs: el couleur el kbira, el menu 3al lisar wala 3al limin, nhar wala lil, fond ytharrek wala la.",
        "Enregistrer: kol les pages yetbaddlou fissa3."]),
    "duplicates": ("Lawwej w lemm les formations elli m3awdin", [
        "Formations › « Trouver les doublons » › Analyser.",
        "Kol zouz, 9arenhom, ba3d fusionner fil wa7da elli t7eb tkhalliha: les candidats, les classes w el plan yemchiw m3aha."]),
    "find": ("Lawwej 3la ay chay fissa3", [
        "El case mta3 el recherche el fou9, wala Ctrl+K fi ay page.",
        "Ekteb esm, email, page, réglage wala chnowa t7eb ta3mel, w 7ell el ligne."]),
    "professor-absence": ("Sajjel ghiyeb formateur", [
        "Classes › Formateurs › carte el formateur › Absence.",
        "Ikhtar el dates (w les heures ken mouch nhar kemel) w el sabab, ba3d Enregistrer.",
        "Les classes mte3ou fi les ayyem hedhom yban fihom, w tnajem tikhtar 7add yo93ed blastou."]),
    "professor-delete": ("Fasa5 formateur", [
        "Classes › Formateurs › carte el formateur › bouton el poubelle.",
        "Kol classe mte3ou, ikhtar chkoun y9arriha tawa (ken les formateurs elli y9arriw el formation mte3ha w far8in fi wa9tha), wala khalliha testanna formateur.",
        "« Supprimer définitivement »: l'accès, les absences w el conversation mte3ou yetfas5ou m3ah; les classes elli kemlou ykhallou esmou."]),
    "professor-access": ("A3ti formateur accès mte3ou", [
        "Classes › Formateurs › carte el formateur › Accès.",
        "Ekteb el email elli yodkhel bih w mot de passe louwel, ba3d a3tihoulou enti.",
        "Ba3d ychouf ken les classes mte3ou: el cours, la présence w les messages mte3ou."]),
    "knowledge-teach": ("3allem el assistant réponse, page wala texte", [
        "Boîte de réception › Connaissances.", "« Apprendre une nouvelle réponse »: sou2al w jweb mte3ou, bel klem mte3ek.",
        "« Apprendre d'un lien ou d'un texte »: a3ti page (9antra.tn, Google, LinkedIn, Facebook…) wala 7ott el texte mte3ha; yesta3melha m3a el source mte3ha."]),
    "assistant-rules": ("9oul lel assistant chnowa ma yjawebch 3lih wa7dou", [
        "Paramètres › Règles de l'assistant.",
        "Sujet fi kol ligne fi « Ne jamais répondre seul à ceci » (takhfidh, rembourssement…): el messages hedhom youslou lik.",
        "« Ce qu'il doit savoir ou dire »: fikra fi kol ligne, ta3tihelou kol marra yekteb."]),
    "facebook": ("Arbot el page Facebook (publications, vidéos, avis)", [
        "Ken admin mta3 el page ynajem ya3mel el access token: Meta for Developers › el application › « Page access token ».",
        "Ektbou FACEBOOK_PAGE_ACCESS_TOKEN fil fichier .env, ba3d 3awed chaghel l'application.",
        "Ba3d, les publications, les vidéos w les avis yet9raw m3a el site kol 6 swaye3, w el assistant ya3refhom."]),
    "website": ("Jib chnowa tbaddel fi 9antra.tn", [
        "Site web › « Lire le site maintenant » (yet9ra zeda kol 6 swaye3).",
        "Win el formation mte3ek t9oul 7aja okhra, ikhtar mte3ek wala mta3 el site."]),
    "notifications": ("Esta3mel les notifications", [
        "El jaras el fou9: kol notification t7ellek el blasa bidhabt, mdawwa.",
        "Fama elli fiha bouton y7ell el mochkla fissa3; « Tout marquer comme lu » yfarragh el jaras."]),
    "translations-fix": ("Sa77a7 traduction", [
        "Paramètres › Langue › « Lire et corriger les traductions ».",
        "Lawwej 3al texte, ekteb el klem mte3ek w Enregistrer: ma yetbaddel 3ad."]),
    "session-link": ("7added el réunion mta3 séance", [
        "7ell el classe › Présence › el séance.",
        "7ott el adresse mta3 el réunion (Meet, Zoom, Teams), wala khalli salle privée tet3mel.",
        "« Envoyer à chaque candidat son propre lien »: kol wa7ed yodkhel bih, w wa9t wsoulou yetsajjel."]),
}

# What the guide says around the steps, in the language of the question (tn: Tunisian as it is typed, in Latin letters).
SAY = {
    "en": {"take": "Take me there", "form": "Open the form", "list": "I did not find how to do that. I can guide you to: "},
    "fr": {"take": "M'y emmener", "form": "Ouvrir le formulaire", "list": "Je n'ai pas trouvé comment faire cela. Je sais guider pour : "},
    "ar": {"take": "خذني إلى هناك", "form": "فتح الاستمارة", "list": "لم أجد كيفية فعل ذلك. يمكنني إرشادك إلى: "},
    "tn": {"take": "Hezzni ghadi", "form": "7el el formulaire", "list": "Ma l9itech kifech na3mel hedhi. Najjem nwarrik: "},
}

# How somebody registers, in Arabic and in Tunisian (French and English are written in howto.answer).
REGISTER = {"ar": ("للتسجيل، يملأ المترشح استمارة التسجيل: ", "شارك هذا الرابط (WhatsApp، Facebook، البريد): يصل التسجيل تلقائيا إلى المترشحين وتُرسل رسالة الترحيب.",
                "يُغيَّر الرابط في الإعدادات › أرقام المركز."),
         "tn": ("Bech yetsajjel, el motarasha7 ya3mer el formulaire mta3 el inscription: ",
                "Ab3ath el lien hedha (WhatsApp, Facebook, email): el inscription toussel wa7adha l Candidats w message de bienvenue yetb3ath.",
                "El lien tbadlou fi Paramètres › Les numéros du centre.")}

# What is still missing: the head of the list, when nothing is missing, the button.
MISSING_HEAD = {"en": ("Still missing ({0})", "Nothing is missing: everything is in order.", "See it all"),
         "fr": ("Ce qui manque encore ({0})", "Rien ne manque : tout est en ordre.", "Tout voir"),
         "ar": ("ما ينقص بعد ({0})", "لا شيء ينقص: كل شيء على ما يرام.", "عرض الكل"),
         "tn": ("Chnowa mazel na9es ({0})", "Ma fama chay na9es: kol chay mrigel.", "Chouf kol chay")}

# Yasmine's own words in Tunisian (services/friend.py has French, Arabic and English).
FRIEND_WORDS = {
    "morning": "Sbe7 el khir", "afternoon": "Aslema", "evening": "Msa el khir",
    "intro": "Ena {name}, sa7btek fi 9antra. Kol nhar n9ollek chnowa yestanna fik, nwarrik kol page, w tnajjem tes2alni 3la ay 7aja.",
    "emails": "{n} email yestannaw jweb mennek.",
    "whatsapp": "{n} message WhatsApp yestannaw bech yetb3athou.",
    "team": "{n} message mel équipe yestannaw fik.",
    "proofs": "{n} preuve de paiement lezem tchoufhom.",
    "mine": "{n} message jdid mel centre lik.",
    "todo": "{n} 7ajet mohemmin el youm. Lowla: {first}",
    "session": "El youm, séance mta3 {name}{at}.",
    "reminder": "El rappel mte3ek « {subject} » yemchi el youm{at}.",
    "owing": "{n} candidat mazelou 3lihom {amount} lkol.",
    "levels": "{n} test de niveau yestannaw jweb.",
    "new": "{n} inscription jdida el jem3a hedhi. Bravo!",
    "missing": "Mazelou {n} 7ajet na9sin (formations, classes, formateurs, réglages): nwarrihomlek.",
    "fine": "Kol chay mrigel el youm. Ya3tik essa7a!",
    "page": "Fi el page hedhi: {title}. {text}",
    "open": "7ell", "next": "Ba3d", "ask": "Es2alni…", "voice_on": "El sout mcha3el", "voice_off": "El sout msakker",
    "news": "Notification jdida: {title}",
    "thinking": "Nlawej…", "sorry": "Ma najjamtech njaweb. 3awed ba3d chwaya.",
    "hello_again": "Ena hna ken t7tajni.",
    "cheers": ["Rta7 chwaya w echreb kes ma: hedha zeda y3awnek tekhdem mli7.",
               "Kol candidat t3awnou el youm, khedma mta3 ghodwa.",
               "Chwaya b chwaya, kol chay yemchi. Nkamlou m3a ba3dhna?"],
}
