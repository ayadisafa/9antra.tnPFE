"""The check questions of the level check a candidate answers before a program starts.

Written by hand, one small set per subject, in French (the language of the
emails candidates receive). Nothing here comes from a language model: a
question that is wrong would give a wrong level.

Each set climbs three steps:

    1  the bases     what somebody who opened the subject once knows
    2  practice      what somebody who has used it knows
    3  advanced      what somebody who built something real with it knows

A question is (step, question, choices) and **the first choice is the right
one**; the order is mixed when a candidate's form is prepared. The page adds
"Je ne sais pas encore" to every question, so nobody has to guess.
"""
from __future__ import annotations

import re

PYTHON = [
    (1, 'Que fait `print(len("Bonjour"))` ?',
     ["Affiche 7", "Affiche Bonjour", "Affiche 6", "Provoque une erreur"]),
    (1, "Quel est le type de la valeur `3.14` en Python ?",
     ["float", "int", "str", "bool"]),
    (1, "Comment écrit-on un commentaire sur une ligne en Python ?",
     ["# un commentaire", "// un commentaire", "<!-- un commentaire -->", "/* un commentaire */"]),
    (2, "Que contient `nombres` après `nombres = [1, 2, 3]` puis `nombres.append(4)` ?",
     ["[1, 2, 3, 4]", "[4, 1, 2, 3]", "[1, 2, 3]", "[[1, 2, 3], 4]"]),
    (2, 'Quelle structure associe des clés à des valeurs, comme `{"nom": "Sami", "age": 22}` ?',
     ["Un dictionnaire", "Une liste", "Un tuple", "Un ensemble (set)"]),
    (2, "À quoi sert un bloc `try: ... except: ...` ?",
     ["À gérer une erreur sans arrêter le programme", "À répéter un bloc plusieurs fois",
      "À définir une fonction", "À importer un module"]),
    (3, "Que renvoie `[x * 2 for x in range(3)]` ?",
     ["[0, 2, 4]", "[2, 4, 6]", "[1, 2, 3]", "[0, 1, 2, 0, 1, 2]"]),
    (3, "Dans une classe Python, à quoi sert la méthode `__init__` ?",
     ["À initialiser l'objet au moment de sa création", "À supprimer l'objet de la mémoire",
      "À importer la classe dans un autre fichier", "À afficher l'objet avec print"]),
]

DEVOPS = [
    (1, "À quoi sert Git ?",
     ["À suivre l'historique des modifications du code", "À héberger un site web",
      "À compiler un programme", "À surveiller un serveur"]),
    (1, "Sous Linux, quelle commande affiche le contenu d'un dossier ?",
     ["ls", "cd", "pwd", "rm"]),
    (1, "Qu'est-ce qu'un conteneur Docker ?",
     ["Un environnement isolé qui contient une application et ses dépendances",
      "Une machine virtuelle complète, avec son propre noyau", "Un dépôt de code source", "Un service de sauvegarde"]),
    (2, "Quelle commande liste les conteneurs Docker en cours d'exécution ?",
     ["docker ps", "docker images", "docker build", "docker pull"]),
    (2, "Dans « CI/CD », que veut dire l'intégration continue (CI) ?",
     ["Chaque modification du code est construite et testée automatiquement",
      "Le code est mis en production une fois par an", "Les serveurs sont redémarrés chaque nuit",
      "Chaque développeur garde son code sur son ordinateur"]),
    (2, "À quoi sert un fichier `Dockerfile` ?",
     ["À décrire comment construire une image", "À lancer plusieurs conteneurs ensemble",
      "À stocker les mots de passe", "À configurer le pare-feu"]),
    (3, "Dans Kubernetes, quel objet maintient le nombre voulu de copies (replicas) d'une application ?",
     ["Un Deployment", "Un Service", "Un ConfigMap", "Un Namespace"]),
    (3, "Que décrit-on avec Terraform ?",
     ["L'infrastructure (serveurs, réseaux…) sous forme de code", "Les tests unitaires d'une application",
      "Les pages d'un site web", "Les tâches planifiées d'un serveur"]),
]

FLUTTER = [
    (1, "Dans quel langage écrit-on une application Flutter ?",
     ["Dart", "Kotlin", "Swift", "JavaScript"]),
    (1, "Dans Flutter, comment appelle-t-on les éléments de l'interface (texte, bouton, image…) ?",
     ["Des widgets", "Des activités", "Des fragments", "Des balises"]),
    (1, "Que permet Flutter avec un seul code source ?",
     ["Créer l'application pour Android et pour iOS", "Créer uniquement des sites web",
      "Créer uniquement pour Android", "Remplacer la base de données"]),
    (2, "Quelle est la différence entre un StatelessWidget et un StatefulWidget ?",
     ["Le StatefulWidget garde un état qui peut changer et redessiner l'écran",
      "Le StatelessWidget est réservé à iOS", "Le StatefulWidget ne peut pas afficher de texte",
      "Il n'y a aucune différence"]),
    (2, "Quelle méthode demande à Flutter de redessiner un StatefulWidget après un changement ?",
     ["setState()", "initState()", "dispose()", "build()"]),
    (2, "Quel widget place ses enfants les uns sous les autres, verticalement ?",
     ["Column", "Row", "Stack", "Container"]),
    (3, "En Dart, comment appelle-t-on une API sans bloquer l'interface ?",
     ["Avec async / await sur un Future", "Avec une boucle while", "Avec le mot-clé final", "Avec un widget Column"]),
    (3, "Quand une API protège ses routes par un jeton (JWT), où l'application envoie-t-elle ce jeton ?",
     ["Dans l'en-tête Authorization de chaque requête", "Dans le fichier pubspec.yaml",
      "Dans le nom de l'application", "Dans un widget Text caché"]),
]

MERN = [
    (1, "Quel langage fait fonctionner à la fois React et Node.js ?",
     ["JavaScript", "Python", "PHP", "Java"]),
    (1, "En HTML, quelle balise crée un lien cliquable ?",
     ["<a>", "<link>", "<href>", "<p>"]),
    (1, "En JavaScript, quel mot-clé déclare une variable que l'on ne pourra pas réaffecter ?",
     ["const", "let", "var", "static"]),
    (2, "Dans React, à quoi sert `useState` ?",
     ["À garder une donnée qui met l'affichage à jour quand elle change", "À interroger une base de données",
      "À créer une route", "À installer un paquet"]),
    (2, "Dans une API REST, quelle méthode HTTP crée une nouvelle ressource ?",
     ["POST", "GET", "DELETE", "HEAD"]),
    (2, "Que fait la commande `npm install` ?",
     ["Elle installe les dépendances listées dans package.json", "Elle démarre le serveur",
      "Elle met le site en ligne", "Elle crée une base de données"]),
    (3, "Dans Express, qu'est-ce qu'un middleware ?",
     ["Une fonction exécutée entre la requête et la réponse", "Une table de la base de données",
      "Un composant React", "Un fichier de style"]),
    (3, "Sous quelle forme MongoDB enregistre-t-il les données ?",
     ["Des documents proches du JSON, regroupés en collections", "Des tables aux colonnes fixes",
      "Des feuilles de calcul", "Des pages HTML"]),
]

SPRING = [
    (1, "Quel langage utilise-t-on le plus souvent avec Spring Boot ?",
     ["Java", "Python", "PHP", "Dart"]),
    (1, "Dans quel langage écrit-on une application Angular ?",
     ["TypeScript", "Java", "Python", "C#"]),
    (1, "En HTML, à quoi sert la balise `<form>` ?",
     ["À regrouper des champs à envoyer", "À afficher une image", "À créer un tableau", "À mettre un texte en gras"]),
    (2, "Dans Angular, comment appelle-t-on la brique qui réunit un template HTML, un style et une classe ?",
     ["Un composant", "Un service", "Un pipe", "Une directive"]),
    (2, "Dans Spring Boot, que fait l'annotation `@RestController` ?",
     ["Elle déclare une classe qui répond à des requêtes HTTP", "Elle crée une table dans la base",
      "Elle lance les tests", "Elle chiffre les mots de passe"]),
    (2, "Que signifie CRUD ?",
     ["Create, Read, Update, Delete", "Compile, Run, Upload, Deploy",
      "Copy, Rename, Undo, Duplicate", "Connect, Request, Use, Disconnect"]),
    (3, "Dans Spring, à quoi servent JPA et Hibernate ?",
     ["À relier des classes Java aux tables de la base de données", "À dessiner l'interface",
      "À gérer les routes d'Angular", "À compresser le JavaScript"]),
    (3, "À quoi sert un jeton JWT dans une application web ?",
     ["À prouver, à chaque requête, que l'utilisateur est bien connecté", "À accélérer l'affichage des images",
      "À traduire l'interface", "À sauvegarder la base de données"]),
]

BI = [
    (1, "À quoi sert un tableau de bord (dashboard) ?",
     ["À voir d'un coup d'œil les chiffres importants", "À saisir des factures",
      "À envoyer des emails", "À protéger un réseau"]),
    (1, "Dans un tableur, quelle fonction additionne une colonne de nombres ?",
     ["SOMME (SUM)", "MOYENNE (AVERAGE)", "NB (COUNT)", "MAX"]),
    (1, "En SQL, quelle instruction lit des données dans une table ?",
     ["SELECT", "INSERT", "UPDATE", "DELETE"]),
    (2, "Que signifie ETL ?",
     ["Extraire, Transformer, Charger", "Évaluer, Tester, Livrer", "Écrire, Traduire, Lire", "Exporter, Trier, Lister"]),
    (2, "En SQL, à quoi sert `GROUP BY` ?",
     ["À regrouper les lignes pour calculer un total ou une moyenne par groupe", "À trier les lignes par ordre alphabétique",
      "À renommer une colonne", "À relier deux bases de données"]),
    (2, "Dans Power BI, comment s'appelle le langage des mesures calculées ?",
     ["DAX", "VBA", "HTML", "Bash"]),
    (3, "Dans un modèle en étoile (Kimball), que contient la table de faits ?",
     ["Les mesures chiffrées (ventes, quantités…) et les clés vers les dimensions", "Uniquement les noms des clients",
      "Les mots de passe des utilisateurs", "Le code des rapports"]),
    (3, "Pourquoi sépare-t-on les données en « entraînement » et « test » avant d'entraîner un modèle ?",
     ["Pour vérifier le modèle sur des données qu'il n'a jamais vues", "Pour doubler la quantité de données",
      "Pour accélérer l'ordinateur", "Pour supprimer les valeurs manquantes"]),
]

IOT = [
    (1, "Qu'est-ce qu'un capteur ?",
     ["Un composant qui mesure une grandeur (température, lumière…)", "Un composant qui stocke des fichiers",
      "Un écran tactile", "Un câble d'alimentation"]),
    (1, "Qu'est-ce qu'un microcontrôleur comme l'ESP32 ou l'Arduino ?",
     ["Une petite carte programmable qui lit des capteurs et commande des composants", "Un système d'exploitation",
      "Un site web", "Un type de batterie"]),
    (1, "Quand une LED s'allume-t-elle ?",
     ["Quand un courant la traverse dans le bon sens", "Quand on la branche dans n'importe quel sens",
      "Quand on la chauffe", "Quand on l'éclaire"]),
    (2, "Dans un programme Arduino ou ESP32, que fait la fonction `loop()` ?",
     ["Elle s'exécute en boucle, sans fin", "Elle s'exécute une seule fois au démarrage",
      "Elle éteint la carte", "Elle téléverse le programme"]),
    (2, "À quoi sert le protocole MQTT ?",
     ["À échanger de petits messages entre objets connectés, par publication et abonnement",
      "À afficher des pages web", "À compresser des images", "À recharger une batterie"]),
    (2, "Qu'apporte l'ESP32 par rapport à une carte Arduino Uno ?",
     ["Le Wi-Fi et le Bluetooth intégrés", "Un écran intégré", "Un disque dur", "Un clavier"]),
    (3, "À quoi sert un signal PWM ?",
     ["À faire varier une puissance : luminosité d'une LED, vitesse d'un moteur", "À mesurer une température",
      "À se connecter au Wi-Fi", "À enregistrer des données"]),
    (3, "Dans MQTT, quel est le rôle du « broker » ?",
     ["Recevoir les messages publiés et les distribuer aux abonnés", "Alimenter les capteurs en électricité",
      "Compiler le programme", "Chiffrer la carte SD"]),
]

CYBER = [
    (1, "Qu'est-ce que le phishing (hameçonnage) ?",
     ["Un message trompeur qui pousse à donner ses identifiants", "Un antivirus",
      "Un type de câble réseau", "Une sauvegarde automatique"]),
    (1, "Qu'est-ce qu'une adresse IP ?",
     ["Le numéro qui identifie une machine sur un réseau", "Le mot de passe du Wi-Fi",
      "Le nom d'un fichier", "La marque de l'ordinateur"]),
    (1, "Lequel de ces mots de passe est le plus solide ?",
     ["T7!mK2#pQ9zR", "123456", "azerty", "motdepasse"]),
    (2, "Quel système est une distribution Linux conçue pour les tests d'intrusion ?",
     ["Kali Linux", "Windows 11", "Android", "macOS"]),
    (2, "À quoi sert l'outil Nmap ?",
     ["À découvrir les machines et les ports ouverts d'un réseau", "À retoucher des images",
      "À rédiger des rapports", "À chiffrer un disque"]),
    (2, "Qu'est-ce qu'une injection SQL ?",
     ["Une saisie malveillante qui modifie la requête envoyée à la base de données", "Une mise à jour de la base",
      "Une sauvegarde chiffrée", "Un type de pare-feu"]),
    (3, "Que cherche un attaquant avec une « élévation de privilèges » ?",
     ["Passer d'un compte limité à un compte administrateur", "Accélérer sa connexion",
      "Changer d'adresse IP", "Trouver l'adresse email de la victime"]),
    (3, "Qu'est-ce que l'OWASP Top 10 ?",
     ["La liste des risques de sécurité les plus courants des applications web", "Les dix antivirus les plus vendus",
      "Un classement de hackers", "Dix commandes Linux à connaître"]),
]

MARKETING = [
    (1, "Que signifie SEO ?",
     ["L'optimisation d'un site pour apparaître dans les moteurs de recherche", "La publicité payante sur Facebook",
      "L'envoi d'emails en masse", "La création de logos"]),
    (1, "Sur les réseaux sociaux, qu'appelle-t-on la « cible » (audience) ?",
     ["Les personnes que l'on veut toucher", "Le budget de la campagne", "Le logo de la marque", "L'heure de publication"]),
    (1, "Quelle est la différence entre une publication « organique » et une publication « sponsorisée » ?",
     ["La sponsorisée est payée pour être montrée à plus de monde", "L'organique contient toujours une vidéo",
      "La sponsorisée n'a pas d'image", "Il n'y en a aucune"]),
    (2, "Qu'est-ce qu'un persona ?",
     ["Le portrait type d'un client idéal", "Un outil de montage vidéo", "Un format d'image", "Le nom d'une campagne"]),
    (2, "Dans une campagne, que mesure le taux de clics (CTR) ?",
     ["La part des personnes qui cliquent parmi celles qui ont vu l'annonce", "Le nombre d'abonnés de la page",
      "Le prix d'un clic", "La durée de la vidéo"]),
    (2, "À quoi sert WordPress ?",
     ["À créer et gérer un site web sans tout programmer", "À retoucher des photos",
      "À envoyer des SMS", "À analyser des tableaux Excel"]),
    (3, "En SEO, qu'est-ce qu'un backlink ?",
     ["Un lien venant d'un autre site, qui renforce la crédibilité du vôtre", "Un lien cassé à supprimer",
      "Un bouton de retour en arrière", "Une publicité payante"]),
    (3, "Dans Meta Ads (Facebook, Instagram), qu'est-ce que le « pixel » ?",
     ["Un petit code placé sur le site pour suivre ce que font les visiteurs", "La taille minimale d'une image",
      "Un type de publication", "Le nom du compte publicitaire"]),
]

AI = [
    (1, "Qu'est-ce qu'un chatbot ?",
     ["Un programme qui répond à des messages en langage naturel", "Un antivirus",
      "Un moteur de base de données", "Un écran tactile"]),
    (1, "Quel langage est le plus utilisé en intelligence artificielle ?",
     ["Python", "HTML", "CSS", "SQL"]),
    (1, "En apprentissage automatique (machine learning), qu'apprend un modèle ?",
     ["Des régularités à partir d'exemples (les données)", "Des règles écrites une à une par le programmeur",
      "Le code source d'Internet", "Rien : il répond au hasard"]),
    (2, "Que signifie NLP (TAL en français) ?",
     ["Le traitement automatique du langage naturel", "Un nouveau langage de programmation",
      "Un protocole réseau", "Une base de données"]),
    (2, "À quoi sert la bibliothèque Pandas ?",
     ["À manipuler et analyser des tableaux de données", "À dessiner des interfaces mobiles",
      "À envoyer des emails", "À créer des sites web"]),
    (2, "Qu'est-ce que la « tokenisation » d'un texte ?",
     ["Son découpage en petites unités (mots ou morceaux de mots)", "Sa traduction en anglais",
      "Son chiffrement par mot de passe", "Sa mise en gras"]),
    (3, "Qu'est-ce que le surapprentissage (overfitting) ?",
     ["Le modèle retient par cœur ses exemples et se trompe sur de nouveaux cas", "Le modèle est trop petit pour apprendre",
      "Les données sont trop nombreuses", "L'ordinateur surchauffe"]),
    (3, "Dans un système de recommandation, que fait le « filtrage collaboratif » ?",
     ["Il recommande ce qu'ont aimé des utilisateurs aux goûts proches", "Il trie les produits par prix",
      "Il supprime les avis négatifs", "Il traduit les descriptions"]),
]

WEB = [
    (1, "À quoi sert le HTML ?",
     ["À structurer le contenu d'une page : titres, paragraphes, liens…", "À stocker des données",
      "À sécuriser un serveur", "À retoucher des images"]),
    (1, "À quoi sert le CSS ?",
     ["À mettre la page en forme : couleurs, tailles, disposition", "À enregistrer les utilisateurs",
      "À envoyer des emails", "À compiler le code"]),
    (1, "Quel langage rend une page interactive dans le navigateur ?",
     ["JavaScript", "SQL", "Python", "Bash"]),
    (2, "Que veut dire « responsive design » ?",
     ["La page s'adapte à la taille de l'écran : téléphone, tablette, ordinateur", "La page répond vite au clic",
      "La page est traduite automatiquement", "La page fonctionne sans Internet"]),
    (2, 'En JavaScript, qu\'affiche `console.log(2 + "2")` ?',
     ['"22"', "4", "NaN", "Une erreur"]),
    (2, "À quoi sert Git ?",
     ["À garder l'historique du code et à travailler à plusieurs", "À héberger des vidéos",
      "À dessiner des maquettes", "À mesurer la vitesse d'un site"]),
    (3, "Qu'est-ce qu'une API REST ?",
     ["Un service auquel on envoie des requêtes HTTP pour lire ou modifier des données", "Une bibliothèque d'icônes",
      "Un éditeur de code", "Un format d'image"]),
    (3, "Que veut dire « déployer » une application ?",
     ["La mettre en ligne sur un serveur pour que les utilisateurs y accèdent", "La supprimer de son ordinateur",
      "L'imprimer", "La traduire"]),
]

SUBJECTS = {
    "python": ("Python", PYTHON),
    "devops": ("DevOps", DEVOPS),
    "flutter": ("Mobile avec Flutter", FLUTTER),
    "mern": ("Web JavaScript (React, Node)", MERN),
    "spring": ("Web Java (Spring Boot, Angular)", SPRING),
    "bi": ("Business Intelligence", BI),
    "iot": ("Objets connectés (IoT)", IOT),
    "cyber": ("Cybersécurité", CYBER),
    "marketing": ("Marketing digital", MARKETING),
    "ai": ("Intelligence artificielle", AI),
    "web": ("Bases du web", WEB),
}

# The formation of the public website a program follows says its subject for sure.
_BY_SLUG = {
    "ai": "ai", "bi": "bi", "cyber-security": "cyber", "devops": "devops", "iot": "iot",
    "digital-marketing": "marketing", "mobile-flutter": "flutter", "mern-stack": "mern",
    "fullstack-spring-angular": "spring",
}

# Otherwise the program's name does: the first line whose words are in it wins.
_BY_WORDS = [
    ("flutter", {"flutter", "dart", "mobile"}),
    ("spring", {"spring", "angular"}),
    ("mern", {"react", "node", "nodejs", "mern", "js", "javascript"}),
    ("devops", {"devops", "docker", "kubernetes"}),
    ("cyber", {"cyber", "cybersecurity", "cybersécurité", "security", "sécurité", "pentest", "pentesting"}),
    ("marketing", {"marketing", "seo"}),
    ("bi", {"bi", "powerbi", "data", "analytics"}),
    ("iot", {"iot", "arduino", "esp32", "embedded", "embarqué"}),
    ("ai", {"ai", "ia", "chatbot", "nlp", "learning", "artificial", "artificielle"}),
    ("python", {"python"}),
    ("web", {"web", "html", "css", "bootcamp", "frontend", "fullstack"}),
]


def subject_of(name: str | None, slug: str | None = None) -> str | None:
    """The subject whose questions fit a program, or None when none does."""
    if slug and slug in _BY_SLUG:
        return _BY_SLUG[slug]
    words = set(re.findall(r"[a-zàâçéèêëîïôöûùüÿœ0-9]+", (name or "").lower()))
    if {"business", "intelligence"} <= words or {"internet", "things"} <= words:
        return "bi" if "business" in words else "iot"
    for subject, hints in _BY_WORDS:
        if words & hints:
            return subject
    return None


def questions_for(name: str | None, slug: str | None = None) -> list[dict]:
    """The check questions of a program: [{"step", "question", "options", "correct_answer": 0}]."""
    subject = subject_of(name, slug)
    if subject is None:
        return []
    return [
        {"step": step, "question": question, "options": list(options), "correct_answer": 0}
        for step, question, options in SUBJECTS[subject][1]
    ]
