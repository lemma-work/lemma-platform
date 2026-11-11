/** The name of the category, not a word for every sentence.
 *
 *  `pod` in the API; "a Lemma" in the product, capitalised, plural "Lemmas"
 *  (decided October 2026, when the frame at `/t` became the app). It was
 *  "teammate" before that, and the old word kept leaking from screens the
 *  frame borrows — the no-access door, run failures, the import panel — so
 *  every screen reads this line, classic ones included.
 *
 *  Said only where the interface names the category itself: New Lemma, the
 *  shelf, Your Lemmas, the search group, and the screens that say one is
 *  missing or closed. Inside the app: when it is acting, say its name ("Kit
 *  wants to run…"); when the sentence is mechanics, drop the subject ("so it
 *  won't load"); when you are already on its page, drop the scope ("Search").
 *  Humans are people. Never swap in agent, assistant, bot or pod — agent
 *  means the things a Lemma hands work to. The landing page is its own voice
 *  and still says "AI teammate". */
export const MATE = "Lemma";
export const MATES = "Lemmas";

export const NEW_MATE = "New " + MATE;

/** The noun for the thing teammates live in.
 *
 *  The code says `organization` because the API does, and the switcher, the
 *  people list and the settings screens all say it out loud already. The
 *  arrival screen briefly said "workspace" instead, which is friendlier and
 *  was a second word for one thing — the mistake [[MATE]] exists to stop
 *  happening twice.
 *
 *  So it is one line, like that one. If it should be "workspace", "company" or
 *  "team", it is this line and the places that read it. */
export const ORG = "organization";

/** A teammate and its space are one pod, seen from two sides.
 *
 *  The teammate is who you talk to: its name and its face, on the rail, at
 *  the top of its sidebar, in "Ask Kit…". The space is what you share: the
 *  pages, tables, apps and workflows it keeps, and the people let into them.
 *  So the word is said only where the place is the point — who can open a
 *  thing, where a thing is kept — and everywhere else the name does the work.
 *
 *  And never bare. Since September 2026 "a space" and "the space" read as
 *  ChatGPT Space, where each person brings their own assistant into a shared
 *  folder. Here the space belongs to the teammate, and the possessive is the
 *  whole difference: "Kit's space", "its space". Where no name is in reach,
 *  "here" says the same thing ("Everyone here can see it").
 *
 *  Never "business agent" either, in any copy: Meta's Business Agent is a
 *  customer-facing bot on WhatsApp, a channel this app is also on.
 *
 *  The agents a teammate hands work to are agents. There are no bots here:
 *  the word only ever named the teammate's own agent, which is the teammate. */
export const SPACE = "space";

/** "Kit’s", and "Follow ups’" — a name ending in s takes the apostrophe
 *  alone, because "Follow ups’s space" is a stumble on the one button that
 *  says whose place it is. */
export function possessive(name: string): string {
    return name + (/s$/i.test(name) ? "’" : "’s");
}
