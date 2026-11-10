"""What is new in the application, release by release: the What's new page, one notification to the admin per release
(the first time the application runs with it), and a line Yasmine says while it is unread.

Each delivery adds its release at the top of RELEASES, with what it brings and where to find it.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

CATEGORY = "WHATS_NEW"

RELEASES = [
    {
        "key": "2026-11-09b", "day": date(2026, 11, 9), "title": "The email assistant tells every kind of message apart, and the iPhone",
        "items": [
            {"icon": "bi-envelope-check", "title": "Yes or no, answers, opinions, the course",
             "text": "A question asked as yes or no is answered yes or no first (Yasmine does the same). Answers to the questions they "
                     "were sent, written their own way, are graded. An opinion is kept as their review, with nothing to answer. A question "
                     "about the course goes to their professor, only once they paid in full. “no cv” means they are fine: nothing to answer.", "url": "/inbox"},
            {"icon": "bi-person-vcard", "title": "Everything about the candidate who writes",
             "text": "Their payments (when, how, what is left), the receipts they sent and what became of them, how to pay, their "
                     "place in a class or why they wait, their level check, weekly questions and presence: the email assistant reads "
                     "them to answer, every figure checked.", "url": "/inbox"},
            {"icon": "bi-phone", "title": "On your iPhone",
             "text": "The pages fit the notch and the home bar, fields no longer zoom, and every SMS opens Messages with the text "
                     "already written: you press Send. How to open it on your iPhone: How it is working, On your phone.",
             "url": "/system#phone"},
        ],
    },
    {
        "key": "2026-11-09", "day": date(2026, 11, 9), "title": "Yasmine goes further, and every professor has their own space",
        "items": [
            {"icon": "bi-chat-left-heart", "title": "She remembers, takes you anywhere, and asks when it is unclear",
             "text": "Your conversation with Yasmine goes on from page to page and from one day to the next (“where were we?”). Ask her "
                     "to open a page or someone's file and she takes you there; “show me around, by yourself” visits every page. "
                     "“Prepare an email for Fares: …” opens the email already written. A vague question gets a short question back, "
                     "with choices to tap.", "url": "/"},
            {"icon": "bi-images", "title": "Answers drawn as moving cards, only what you asked",
             "text": "Figures come as tiles that count up, shares as rings, amounts as bars, people as chips, dates on a line, "
                     "articles and web pages as links: only what the answer says. “What can you do?” shows every ability as a card to "
                     "try, and what she does not do yet. She also tells jokes, and searches the job market, the journal and the web "
                     "(never with a person's name).", "url": "/"},
            {"icon": "bi-person-video3", "title": "Every professor has their own space",
             "text": "Professors, in the menu: each one's classes, what candidates thought of them, the messages and what waits for "
                     "them (an absence without a replacement, sheets not filled, questions waiting, a link missing). Professors see "
                     "their own in “My space”, say when they will be away, and write to their classes.", "url": "/professors"},
            {"icon": "bi-calendar2-week", "title": "A professor away: another professor, another day, or a week later",
             "text": "When nobody can replace them, choose on the class page: another professor, another day, or a week later (the "
                     "sessions after it follow, the candidates are told). Or let it go a week later by itself (Settings, Classes). "
                     "A candidate whose next class would then start too soon moves to a later one.", "url": "/settings#replacement-fallback"},
            {"icon": "bi-alarm", "title": "Reminders: several times, and written for you",
             "text": "One reminder can go at several times. Say what it is about and Yasmine writes it (no date or figure you did not "
                     "give: those are left for you to fill).", "url": "/reminders#new"},
        ],
    },
    {
        "key": "2026-11-08b", "day": date(2026, 11, 8), "title": "Yasmine and the email assistant understand any question",
        "items": [
            {"icon": "bi-stars", "title": "Any question, in any wording or language",
             "text": "A question to Yasmine or a candidate's email is now understood by a cloud model, in French, English, Arabic or "
                     "Tunisian: it picks what to look up in your records (classes, sessions, payments, professors, programs, the "
                     "centre, what you taught) and writes the answer from that only. Every figure, date, link and phone number is "
                     "checked against your records; an answer with something they do not show is never given.", "url": "/settings#cloud-reader"},
            {"icon": "bi-shield-check", "title": "Only checked answers go alone",
             "text": "A candidate gets an answer alone only when every point of their message was answered from checked facts. "
                     "Something your records do not say (an internship, paying in instalments…) is never guessed: the answer "
                     "waits for you, saying which point. Refunds, cancellations and complaints always come to you, with no draft. "
                     "When the free daily quota is used up, answers wait for you until the next day.", "url": "/inbox"},
        ],
    },
    {
        "key": "2026-11-08", "day": date(2026, 11, 8), "title": "Yasmine knows what to do when someone may drop out, and in other cases",
        "items": [
            {"icon": "bi-person-heart", "title": "Someone may drop out: who, why, and what to do",
             "text": "Ask Yasmine “someone is about to drop out, what can I do?” in any language: she says what to do for each reason "
                     "(absences, questions not answered, results, money owed, what they wrote, wanting to stop), then who may drop out "
                     "right now, with the message already prepared for each and when to send it. Name someone and she answers for them.",
             "url": "/#live-dashboard-risk"},
            {"icon": "bi-signpost-split", "title": "What to do when…",
             "text": "A professor absent, a candidate who has not paid, a class without a professor, an unhappy candidate, a refund, a "
                     "missing meeting link, a message that did not arrive: ask “what do I do?” and she says what the application "
                     "already does by itself, what is left to you, and how things stand now.", "url": "/"},
        ],
    },
    {
        "key": "2026-11-07e", "day": date(2026, 11, 7), "title": "Meeting links before the start, and a replacement for the day",
        "items": [
            {"icon": "bi-camera-video", "title": "The meeting link, 3 days before the start",
             "text": "A class is fully online, fully on site, or both. Three days before an online or mixed class starts, its professor "
                     "is asked for the private meeting link (in the application, by email, WhatsApp and SMS). Once given, every "
                     "session uses it and each online candidate gets their own link; those on site get none. Still none a day "
                     "before: you are told.", "url": "/classes/"},
            {"icon": "bi-arrow-left-right", "title": "A professor away: a replacement for that day only",
             "text": "When you note an absence, each of their sessions that day asks a professor who teaches it and is free then to "
                     "confirm, with a private link. Yes: they replace for that session only. No, or no answer in time: the next one "
                     "is asked. You get one notification until it is settled.", "url": "/classes/?tab=professors"},
            {"icon": "bi-lock", "title": "One class at a time at the same hours",
             "text": "A professor whose class still runs is never given another class at the same hours until the whole class is "
                     "over, even when it runs past its planned end.", "url": "/classes/?tab=professors"},
        ],
    },
    {
        "key": "2026-11-07d", "day": date(2026, 11, 7), "title": "Professors take their class by themselves; reminders before each session",
        "items": [
            {"icon": "bi-person-check", "title": "A free professor takes the class, and is told",
             "text": "A class waiting for a professor takes the first one who teaches its program and is free on its start day: no other "
                     "class at its hours, not away that day. They are told at once in the application, by email, WhatsApp and SMS, "
                     "with the class's code, its hours and their space.", "url": "/classes/?tab=professors"},
            {"icon": "bi-easel", "title": "Reminders every day, or before each session",
             "text": "Your own reminders can now go every day, or a number of hours before each of the candidates' sessions, naming "
                     "that session's day, hour and topic. “Reset” starts a reminder again, as if just written, even a stopped one.",
             "url": "/reminders#new"},
        ],
    },
    {
        "key": "2026-11-07c", "day": date(2026, 11, 7), "title": "A class the moment they pay",
        "items": [
            {"icon": "bi-calendar-plus", "title": "Placed the moment they pay, you are only told",
             "text": "However the payment arrives (recorded, a proof confirmed, the form, the quick confirm), a fully paid candidate joins "
                     "an open class of their program, or a new class is made for them. Someone already in a class is placed at once "
                     "in a class that starts some days after it ends (one program at a time), instead of waiting for your decision. "
                     "You get one notification with the class's code.", "url": "/classes/"},
        ],
    },
    {
        "key": "2026-11-07b", "day": date(2026, 11, 7), "title": "Yasmine sums up, answers short, with moving pictures",
        "items": [
            {"icon": "bi-text-paragraph", "title": "Sum it up",
             "text": "Ask Yasmine to sum up a candidate, their discussion, a class, a program, the day, the week, the page you are on, a text "
                     "pasted after the word, or her last answer, in any language. From the records, in a few lines, with the place to open.",
             "url": "/assistant/agents#graph"},
            {"icon": "bi-chat-left-dots", "title": "Short answers first",
             "text": "A long answer shows its first lines, or for a list its first names and how many more; “More” opens the whole of it. "
                     "Her voice reads the short one.", "url": "/"},
            {"icon": "bi-images", "title": "A moving picture with each answer",
             "text": "Each answer comes with the animated picture of its subject (payments, sessions, candidates…), and the news with a "
                     "real photo from the journal that slowly moves.", "url": "/"},
        ],
    },
    {
        "key": "2026-11-07", "day": date(2026, 11, 7), "title": "Reviews in ten seconds, a simpler inbox, the journal and the stories",
        "items": [
            {"icon": "bi-star-fill", "title": "Reviews in ten seconds",
             "text": "The five stars are inside the email and the WhatsApp: one tap gives the rating, a few words and the trainer come after "
                     "only if they want. Asked on the last session day, with a QR code to show in the room. Who gives 1 to 3 stars is "
                     "thanked privately and you are asked to call them; who gives 4 or 5 is offered Google and Facebook, their words copied.",
             "url": "/website#feedback"},
            {"icon": "bi-trophy", "title": "Celebrate a success",
             "text": "An internship, a PFE, a certification or a job: from the candidate's page, congratulate them and ask, at that happy "
                     "moment, for a review on Google or Facebook. The success becomes a post idea on the Social media page.", "url": "/social#create"},
            {"icon": "bi-inbox", "title": "A simpler inbox",
             "text": "Three tabs: what needs you, what needs no reply, every conversation. One line says what waits; the rest (how messages "
                     "go, the figures, the tools) is behind “⋯”. The assistant's answers are signed Yasmine.", "url": "/inbox"},
            {"icon": "bi-chat-heart", "title": "Yasmine tells you what happened, and shows you around",
             "text": "Back after a while, she says what came in and what went out meanwhile. Ask her “what happened today?”. "
                     "“Show me around” takes you from page to page, the next one calling in the menu.", "url": "/"},
            {"icon": "bi-newspaper", "title": "The journal, on every page",
             "text": "The newspaper at the top opens the latest news of each field you teach, with moving photos, read again every six "
                     "hours. Professors find their corner there: the news of what they teach and what could make their courses better.", "url": "/"},
            {"icon": "bi-fire", "title": "Stories and days in a row",
             "text": "The dashboard opens with stories to tap (new people, reviews, money in, sessions, news, successes), full screen, and "
                     "the days in a row the team opened the application. Videos get a “Hook” style: a punch line first, fast cuts, a loop.",
             "url": "/"},
        ],
    },
    {
        "key": "2026-11-04b", "day": date(2026, 11, 4), "title": "Who is connected, and a livelier application",
        "items": [
            {"icon": "bi-broadcast", "title": "Who is using the application",
             "text": "Live: who is online, idle or away, on which page, from which browser and address. Sign out one browser, one person "
                     "everywhere, or everyone else: their page goes to the sign-in page at once. Ask Yasmine “who is connected?” too.",
             "url": "/settings/team#connected"},
            {"icon": "bi-chat-heart", "title": "Yasmine's window, livelier",
             "text": "A welcome with four things to ask in one tap, messages that come in with her initial and the time, a typing bubble while "
                     "she thinks, and the quick questions in a dock above the box.", "url": "/"},
            {"icon": "bi-window-stack", "title": "One part at a time, with moving pictures",
             "text": "The dashboard keeps what needs you on top, the rest in four tabs (Today, People, Classes, Growth). Settings, each class "
                     "and the website have tabs too, and every page opens with its own moving picture.", "url": "/"},
        ],
    },
    {
        "key": "2026-11-04","day": date(2026, 11, 4), "title": "Yasmine talks like a person",
        "items": [
            {"icon": "bi-chat-heart", "title": "How are you? Fine, and you?",
             "text": "Yasmine answers a greeting, “how are you”, thanks, “who are you”, how you feel and a goodbye like a person, in the "
                     "language you wrote in (French, English, Arabic or Tunisian), with your first name, and tells you what waits today.",
             "url": "/assistant/agents#graph"},
            {"icon": "bi-signpost-split", "title": "Small talk or a question",
             "text": "“Ça va ?” is small talk; “ça va les paiements ?” is a question about payments. “Salut ! qui a payé ?” gets a hello, "
                     "then the answer. “Who has paid?” is no longer read as “who has not paid?”.", "url": "/assistant/agents#try"},
        ],
    },
    {
        "key": "2026-11-03c","day": date(2026, 11, 3), "title": "The assistant reads every request; who takes part",
        "items": [
            {"icon": "bi-list-check", "title": "Nothing missed in an email",
             "text": "Each thing a candidate asks is read on its own. An answer that leaves one out is not sent: it waits for you, saying "
                     "which request is missing.", "url": "/inbox"},
            {"icon": "bi-journal-code", "title": "The agent of their program answers",
             "text": "The email assistant asks the agent of the program the candidate follows: its weeks, its professors and what you "
                     "told about it. The Agents page counts what each program's agent answered.", "url": "/assistant/agents"},
            {"icon": "bi-people", "title": "Who takes part, and the professor",
             "text": "On each class page: who takes part most (what the professor noted and the questions asked), who arrives late, and "
                     "how the professor follows the class (sheets confirmed in time, questions answered and how fast, their stars).",
             "url": "/classes/"},
            {"icon": "bi-window-stack", "title": "Social media, one part at a time",
             "text": "Tabs, each with its picture and three steps. When and what to post is now a light help in its own tab, and soft on the "
                     "dashboard.", "url": "/social"},
        ],
    },
    {
        "key": "2026-11-03b","day": date(2026, 11, 3), "title": "Follow-up: nothing lost, less noise",
        "items": [
            {"icon": "bi-send-x", "title": "What didn't reach them",
             "text": "Emails that failed and WhatsApp messages waiting for your phone, in one list with why and what to do: send again, "
                     "open WhatsApp, call, or not needed any more. A failed email is tried again once by itself.", "url": "/inbox/not-reached"},
            {"icon": "bi-person-check", "title": "Presence in one tap",
             "text": "After an online session, who never came in through their link is marked absent, and the professor is asked once to "
                     "confirm the sheet, already filled in from the links, in one tap.", "url": "/classes/"},
            {"icon": "bi-envelope-paper", "title": "One letter a week, and a limit",
             "text": "Each Monday, one message with the week's sessions and the questions still waiting. A candidate gets at most 4 "
                     "automatic messages in seven days; past that, what can wait goes into the letter.", "url": "/inbox/templates"},
            {"icon": "bi-alarm", "title": "Reminders that catch up",
             "text": "A class formed after the moment of its first reminder: the closest one goes at once, saying the days really left.",
             "url": "/classes/"},
            {"icon": "bi-bell", "title": "Notifications that close themselves",
             "text": "The bell rings only for what needs you, and each one closes once it is done, wherever you did it. What only tells "
                     "you something closes by itself after three days, and the same one is never shown twice.", "url": "/"},
        ],
    },
    {
        "key": "2026-11-03","day": date(2026, 11, 3), "title": "The agents work as a LangGraph graph",
        "items": [
            {"icon": "bi-diagram-3", "title": "Every question travels through one graph",
             "text": "The assistant's window and the guide window now run on LangGraph: each agent is a step, and the rules choose the next "
                     "step, never a model. The answers stay the same.", "url": "/assistant/agents#graph"},
            {"icon": "bi-signpost-2", "title": "See the way a question goes",
             "text": "The Agents page draws the graph from the graph itself. Try a sentence: the step it goes to lights up.",
             "url": "/assistant/agents#try"},
            {"icon": "bi-spellcheck", "title": "Yasmine says whom she asked",
             "text": "Under an answer, her window now shows the agents she asked and the words she read put right; they were left out before.",
             "url": "/assistant/agents"},
        ],
    },
    {
        "key": "2026-11-02d", "day": date(2026, 11, 2), "title": "Posts in Tunisian",
        "items": [
            {"icon": "bi-chat-quote", "title": "Posts, stories and videos in Tunisian",
             "text": "Written the way Tunisians write on social media (“Sajjel tawa”, “9oultou, 3malna”, “Nja7kom howa fakhrna”), first; French, English and "
                     "Arabic stay one click away.", "url": "/social#create"},
            {"icon": "bi-eraser", "title": "No more following the posts' numbers",
             "text": "“How your posts do” and every button to follow a post's numbers are gone: the networks show them in their own statistics. "
                     "The page keeps what is trending, when to post and this week's plan.", "url": "/social#plan"},
        ],
    },
    {
        "key": "2026-11-02c", "day": date(2026, 11, 2), "title": "The system corrects itself",
        "items": [
            {"icon": "bi-question-diamond", "title": "A question is not an opinion",
             "text": "A reply to the “what did you think?” email that asks something is read as a question, not kept as a review; with both, the opinion is "
                     "kept and the question answered.", "url": "/inbox?filter=aside"},
            {"icon": "bi-arrow-counterclockwise", "title": "Your correction corrects every analysis",
             "text": "“It's a question, not an opinion”: the review it was taken for leaves the stars, the mood, the themes and the dashboard, the message comes "
                     "back with an answer prepared, and replies like it are read as questions from then on. “Undo” puts it back.", "url": "/inbox?filter=aside"},
            {"icon": "bi-arrow-repeat", "title": "It reads its own decisions again",
             "text": "Every 30 minutes, and when you ask, the system reads what it set aside and the reviews it kept with what it learnt since: what asks "
                     "something comes back to you, and an opinion that was a question leaves the reviews. Nothing is sent alone.", "url": "/inbox?filter=aside"},
        ],
    },
    {
        "key": "2026-11-02b", "day": date(2026, 11, 2), "title": "Yasmine and her agents: one per program, and an assistant you can question",
        "items": [
            {"icon": "bi-journal-code", "title": "One agent per program",
             "text": "Each program has its own agent, born with the program: what it teaches week by week, its price, hours, next session and professors, "
                     "and what was learnt about it. Yasmine asks the other agents when she does not know, and says whom she asked.", "url": "/assistant/agents#program-agents"},
            {"icon": "bi-chat-square-text", "title": "Ask the email assistant why",
             "text": "Under each answer it sent: “why?” says where the answer came from and how sure it was; “the right answer is …” teaches it for next time, "
                     "and “send it” sends the correction. Also in Yasmine's window: “why did you answer Amira that way?”.", "url": "/inbox?filter=auto"},
            {"icon": "bi-mortarboard", "title": "Professors' answers, reworded and learnt",
             "text": "A technical question goes to the candidate's professor, by email too, and the candidate gets the professor's email. The professor's few words "
                     "become a full answer, and the answer is learnt; a professor can also simply reply to the email.", "url": "/inbox"},
            {"icon": "bi-spellcheck", "title": "Words with mistakes understood",
             "text": "“inscripton”, “paimant”, “formtion pyhton”: read as meant, in the emails and in Yasmine's window, and shown as read.", "url": "/inbox"},
            {"icon": "bi-person-heart", "title": "Change Yasmine by asking her",
             "text": "“Call yourself Salma”, “put on glasses”, “a man's voice”, “wear a blazer”; her settings are also one click away in her window.", "url": "/settings#character"},
            {"icon": "bi-translate", "title": "Every word translated",
             "text": "The last words of the pages written in French and Arabic, pieces of code no longer counted as words, and the local model translating more at a time.", "url": "/settings/translations"},
            {"icon": "bi-stars", "title": "What needs you, alive",
             "text": "Figures count up on every page, what waits for you breathes gently, and each page's main button catches the light once.", "url": "/"},
        ],
    },
    {
        "key": "2026-11-02", "day": date(2026, 11, 2), "title": "Marketing: trends, the best times, and Yasmine who changes the system",
        "items": [
            {"icon": "bi-fire", "title": "What is trending in posts",
             "text": "Counted from the marketing articles the journal reads, and from your own posts; with what posts look like this year.", "url": "/social#trends"},
            {"icon": "bi-palette2", "title": "Posts redesigned",
             "text": "Two new looks, Bold and Bright: big type, strong contrast, crisp shapes. Bold is the one chosen first.", "url": "/social#create"},
            {"icon": "bi-music-note-beamed", "title": "This year's music",
             "text": "Videos now have dance-pop, lo-fi or cinematic music, chosen for each kind of video, unless you choose otherwise.", "url": "/social#videos"},
            {"icon": "bi-clock-history", "title": "When to post",
             "text": "The moments most of your people can interact, from when they write, answer and register, and how your posts did.", "url": "/social#timing"},
            {"icon": "bi-megaphone", "title": "Marketing this week, on the dashboard",
             "text": "Four posts at the best moments and advice from your records, each made in one click or by Yasmine.", "url": "/#dashboard-marketing"},
            {"icon": "bi-sliders2", "title": "Yasmine changes the system",
             "text": "Ask her: “set the confidence to 80%”, “turn off the session reminder”, “the menu on the right”. She says what it was, and “undo” puts it back. "
                     "She also answers everything about your marketing.", "url": "/assistant/agents"},
            {"icon": "bi-diagram-3", "title": "Every functionality, with its schema",
             "text": "Each page explains itself in steps you can click, and “How it works” lists them all.", "url": "/guide#every"},
        ],
    },
    {
        "key": "2026-11-01b", "day": date(2026, 11, 1), "title": "Smart suggestions, and nothing hidden any more",
        "items": [
            {"icon": "bi-moon-stars", "title": "Messages set aside: you decide",
             "text": "A message the assistant set aside without an answer (a thank-you, an automatic reply…) says why. Say it needed an "
                     "answer, with a button or in a few words under it or to Yasmine: it comes back with an answer prepared, and the rule "
                     "changes so the next ones are kept. Every change can be undone.", "url": "/inbox?filter=aside"},
            {"icon": "bi-lightbulb", "title": "Ideas to keep candidates engaged",
             "text": "On the dashboard, under what people say: plans from their feedback and from how the classes go, and what is going on "
                     "in every industry, each with the facts it rests on and a target to reach by a date.", "url": "/#engagement-ideas"},
            {"icon": "bi-stars", "title": "What is smart to remind each candidate",
             "text": "From their presence, their weekly questions, how their week went and how they answer you: what to send, which way, "
                     "at what hour, and what to watch afterwards. The form fills itself from a suggestion.", "url": "/reminders"},
            {"icon": "bi-eye", "title": "Menus and windows always seen in full",
             "text": "A menu opened in a page's head was cut; Yasmine covered the corner of windows and menus; two labels of the Social "
                     "media page were grey on red. All fixed, and every page was checked for what could not be seen.", "url": "/programs/all"},
        ],
    },
    {
        "key": "2026-11-01", "day": date(2026, 11, 1), "title": "Social media, and every feedback in one place",
        "items": [
            {"icon": "bi-magic", "title": "Posts and stories made from your records",
             "text": "Your figures, what former candidates said in public, what you did about feedback, the next sessions: each as a picture "
                     "(post 1:1, story 9:16) and a text with hashtags, in French, English or Arabic.", "url": "/social#create"},
            {"icon": "bi-camera-reels", "title": "Motivating videos with music",
             "text": "Their success and how you answer their feedback, scene after scene, with figures that count up and music composed "
                     "on this computer (or your own). MP4, ready for Facebook, Instagram, TikTok and LinkedIn.", "url": "/social#videos"},
            {"icon": "bi-ear", "title": "We listen: you said, we did",
             "text": "What people asked to improve, and what you did about it, shown in posts and videos.", "url": "/social#listen"},
            {"icon": "bi-graph-up", "title": "The engagement of your posts",
             "text": "Each post's numbers and engagement, compared by kind, subject, network, day and hour, with advice.", "url": "/social"},
            {"icon": "bi-chat-heart", "title": "Every feedback in one place on the dashboard",
             "text": "9antra.tn, the reviews in the application and the weekly check-ins together, with filters and what to improve.", "url": "/"},
        ],
    },
    {
        "key": "2026-10-31", "day": date(2026, 10, 31), "title": "Agents, categories, and the assistant inside Yasmine",
        "items": [
            {"icon": "bi-diagram-3", "title": "A team of agents with an orchestrator",
             "text": "Each question and each message is read for what it is about, and handed to the agent of that subject: "
                     "programs, planning, payment, registration, technical help, complaints, trends, figures, the guide. "
                     "The agents page shows who did what.", "url": "/assistant/agents"},
            {"icon": "bi-tags", "title": "Every message has its category",
             "text": "In the inbox, each email, WhatsApp message and SMS says what it is about; filter by category. A complaint "
                     "always comes to you.", "url": "/inbox"},
            {"icon": "bi-person-hearts", "title": "Yasmine is the assistant",
             "text": "One assistant: click Yasmine to talk. Choose her name, man or woman, the hair, the clothes, the colours, the "
                     "glasses and the voice in Settings.", "url": "/settings#character"},
            {"icon": "bi-mortarboard", "title": "Professors have their assistant",
             "text": "Their classes, their next sessions, who was absent, the course content and the guide. Never payments or "
                     "other classes.", "url": "/assistant/agents#professor"},
            {"icon": "bi-quote", "title": "Answers with their sources, numbered",
             "text": "When no agent knows, the knowledge agent quotes the closest passages of what the application knows, each "
                     "with its number. Ask what is trendy in a field, which services to add, or what a name says.",
             "url": "/assistant/agents#knowledge"},
            {"icon": "bi-bell", "title": "Every reminder on one page, and one before each session",
             "text": "Switch each reminder on or off, change when it goes, rewrite it or put the original back. New: each "
                     "candidate is reminded of their next session (24 hours before by default).", "url": "/reminders#auto"},
            {"icon": "bi-palette2", "title": "Emails with your design",
             "text": "A banner photo, up to three photos side by side, your colour, the font, the button's shape, the corners, "
                     "the background and the icon: for each automatic email and each reminder.", "url": "/inbox/templates"},
            {"icon": "bi-stopwatch", "title": "Questions answered on a page, with a timer",
             "text": "The weekly questions and the questions after a session open on their own page: the time on each question "
                     "and how often the candidate left the page are shown with their answers.", "url": "/quiz/all"},
            {"icon": "bi-chat-left-quote", "title": "Tell the assistant what to answer",
             "text": "In a discussion, give the assistant the information: it writes the answer to the candidate, and can keep "
                     "it for the next ones who ask.", "url": "/inbox"},
            {"icon": "bi-envelope-check", "title": "The mailbox keeps reading",
             "text": "When the mail server cuts the connection, the check tries again at once; it is shown as a problem only "
                     "if it keeps happening.", "url": "/system"},
            {"icon": "bi-signpost-2", "title": "“How this page works”, shown and hidden again",
             "text": "The signpost button at the top of every page shows the short explanation, and hides it again; it stays as you left it.",
             "url": "/candidate/all"},
            {"icon": "bi-phone", "title": "Every page fits a phone", "text": "The pages were checked at phone width.", "url": "/"},
        ],
    },
    {
        "key": "2026-10-27", "day": date(2026, 10, 27), "title": "The centre's details, the program files and the journal",
        "items": [
            {"icon": "bi-receipt", "title": "Payment proofs and cheques read",
             "text": "A photo of a cheque or a receipt: to whom, the amount in figures and in words, the bank, the RIB.",
             "url": "/payments#proofs"},
            {"icon": "bi-folder2-open", "title": "Program files checked",
             "text": "A file added to a course is read: whether it belongs to the program, or to which one.", "url": "/programs/expertise"},
            {"icon": "bi-newspaper", "title": "The journal", "text": "The news of every field you teach, and what it suggests.",
             "url": "/journal"},
            {"icon": "bi-key", "title": "Forgot password", "text": "Everyone can get a link by email to choose a new password.",
             "url": "/account"},
            {"icon": "bi-person-exclamation", "title": "Who may drop out", "text": "On the dashboard, from absences, answers and payments.",
             "url": "/"},
        ],
    },
]


def latest() -> dict:
    return RELEASES[0]


def notification_title(release: dict) -> str:
    return f"What's new: {release['title']}"


def announce(db: Session) -> bool:
    """Tells the admin once about the latest release. Returns whether a notification was made now."""
    from app.models.notification import Notification
    from app.services import notification_service

    release = latest()
    title = notification_title(release)
    if db.query(Notification).filter(Notification.category == CATEGORY, Notification.title == title).first() is not None:
        return False
    notification_service.create_notification(
        db, title=title, message=f"{len(release['items'])} new things in the application. Open What's new to see each one.",
        category=CATEGORY, action_url="/whats-new", payload={"release": release["key"]},
    )
    return True


def unread(db: Session) -> dict | None:
    """The latest release while its notification is unread (Yasmine says it)."""
    from app.models.notification import Notification

    release = latest()
    row = (db.query(Notification).filter(Notification.category == CATEGORY, Notification.title == notification_title(release))
           .first())
    return release if row is not None and not row.is_read else None


def mark_seen(db: Session) -> None:
    from datetime import datetime

    from app.models.notification import Notification

    for row in db.query(Notification).filter(Notification.category == CATEGORY, Notification.is_read.is_(False)).all():
        row.is_read, row.status, row.read_at = True, "read", datetime.utcnow()
    db.commit()
