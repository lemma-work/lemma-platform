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
 *  Inside the app `MATE` is enough. The rail is full of them, the header names
 *  one, and nothing on screen is competing for the word.
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
 *  learns your work", and from the rail onward it is just a teammate. */
export const AI_MATE = "AI " + MATE;
export const AI_MATES = AI_MATE + "s";
