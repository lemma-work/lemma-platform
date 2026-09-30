/** The name of the category, not a word for every sentence.
 *
 *  `pod` in the API; "teammate" only where the interface names the category
 *  itself: hiring (New teammate, the first-run doorway), the rail header, the
 *  search group, and the screens that say one is missing. Everywhere else the
 *  app had started using it as a pronoun — "the teammate cannot load it",
 *  "search this teammate", "waiting for the teammate" — and the word wore out.
 *
 *  So, inside the app: when it is acting, say its name ("Kit wants to run…");
 *  when the sentence is mechanics, drop the subject ("so it won't load");
 *  when you are already on its page, drop the scope ("Search"). Humans are
 *  people, never teammates. Never swap in agent, assistant, bot or pod —
 *  agent means the things a teammate hands work to. The landing page is its
 *  own voice and says "teammate" freely. */
export const MATE = "teammate";
export const MATES = "Teammates";

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

/** The same thing, said to somebody who has not met one yet.
 *
 *  On the way in it is not enough, and for a specific reason: the screens that
 *  introduce a teammate are the same screens that talk about colleagues. "Who
 *  else can see your teammates" is, to somebody on their first morning, a
 *  perfectly good question about their coworkers — and it is sitting directly
 *  above a choice between "Just me" and "My team". The word has to do two jobs
 *  in one sentence and cannot.
 *
 *  So first contact spells it out and the app stops once you are in, which is
 *  the split the landing page already makes: it says "the AI teammate that
 *  learns your work", and from the rail onward it is mostly just a name. */
export const AI_MATE = "AI " + MATE;
export const AI_MATES = AI_MATE + "s";

/** A teammate and its space are one pod, seen from two sides.
 *
 *  The teammate is who you talk to: its name and its face, on the rail, at
 *  the top of its sidebar, in "Ask Kit…". The space is what you share: the
 *  pages, tables, apps and workflows it keeps, and the people let into them.
 *  So the word is said only where the place is the point — who can open a
 *  thing, where a thing is kept — and everywhere else the name does the work.
 *
 *  The agents a teammate hands work to are agents. There are no bots here:
 *  the word only ever named the teammate's own agent, which is the teammate. */
export const SPACE = "space";
