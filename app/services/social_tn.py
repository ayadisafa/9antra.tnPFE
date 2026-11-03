"""The posts' words in Tunisian (Latin letters, the way Tunisians write on social media: 3 = ع, 7 = ح, 9 = ق, 5 = خ).
They are what the posts, stories and videos say, never words of the interface: kept apart so the pages' translation
leaves them as they are (translations._SILENT)."""

WORDS = {"trained": "candidat t9raw m3ana", "training": "y9raw tawa", "programs": "formation", "happy": "farhanin", "stars": "moyenne el note",
         "recommend": "ynas7ouna", "said": "9alou ra2yhom", "classes": "classe khaddama tawa",
         "success_kicker": "YENJ7OU M3ANA", "success_title": "Nja7kom howa fakhrna",
         "testimonial_kicker": "CHNOWA 9ALOU 3LINA", "listen_kicker": "TEHKIW, NESM3OU", "listen_title": "9oultou, 3malna",
         "you_said": "9oultou", "we_did": "3malna", "program_kicker": "SESSION JDIDA", "starts": "Tebda", "weeks": "jem3a",
         "online": "En ligne", "onsite": "Fil centre", "both": "En ligne w fil centre", "trend_kicker": "EL TREND TAWA", "join_kicker": "IJA M3ANA",
         "join_title": "El compétence el jeya mte3ek tebda lehna", "cta": "Sajjel tawa", "centre": "Centre de formation",
         "taught": "mawjoud deja fi", "dt": "DT"}

CAPTIONS = {
    "success": "{brand} 💪 {lines}\n\nY3aychkom 3al thi9a mte3kom. Nja7kom howa akber fakhr lina! 🎓\n\n👉 Ija m3ana: {link}",
    "testimonial": "💬 « {quote} »\n— {name}\n\nY3aychek 3al klem hedha, ykhallina nzidou kol nhar! ❤️\n\n👉 Dourek enti, sajjel: {link}",
    "listen": "👂 Tehkiw, nesm3ou.\n\n9oultou: {said}\n✅ 3malna: {did}\n\nAra2kom tkabberna. Ab9aw ekteblna! 🙏",
    "program": "🚀 Session jdida: {program}!\n📅 {starts}\n⏱️ {weeks}\n📍 {mode}{price}\n\nEl blayes m7doudin, sajjel tawa 👉 {link}",
    "trend": "📈 El kol yahkiw 3lih: {term}.\nKhabar behi: {taught} {program} 3and {brand}! 💡\n\n👉 Chouf el formation: {link}",
    "join": "✨ El compétence el jeya mte3ek tebda lehna.\n{programs}\n\n📝 Inscription: {link}\n📞 {phone}",
}

# The first scene of each kind of video: (kicker, title).
INTRO = {"success": ("YENJ7OU M3ANA", "Nja7hom howa fakhrna"), "listen": ("TEHKIW, NESM3OU", "Ara2kom tkabberna"),
         "both": ("M3A BA3DHNA, AB3ED", "Nja7kom w sotkom"), "program": ("9RIB", "Les sessions el jeyin")}
