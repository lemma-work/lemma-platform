import type { Profile, Skill } from "./types";
import { characterForSeed } from "../shell/cast";

/** What a listing on the shelf carries, and the one listing that is not a
 *  role: somebody new.
 *
 *  The roles themselves are not here. Each is a template the backend ships,
 *  and the shelf reads their cards from it (`roles.ts`, `GET
 *  /pods/bundle/templates`), so a role's card cannot promise a skill or a
 *  measure its template does not bring, and a new role is a new template with
 *  no change to this app. What stays here is the shape both kinds of listing
 *  are read through, and the blank teammate, which is no template at all.
 *
 *  `seed` is the archetype's face. It is not stored on the pod: it picks the
 *  character, and the hire's own id is then searched for a variant landing on
 *  the same one. See `variantForCharacter` — your Follow-ups and mine are the
 *  same role and the same character, on two different pods. A template's
 *  seed is searched against `characterForSeed` when the template is written,
 *  so two roles do not arrive as the same sculpture; the backend's template
 *  tests hold that no two roles share one. */
export interface Hire {
    id: string;
    name: string;
    /** The job, in one line, from the reader's side of the desk. */
    role: string;
    /** The archetype face: tone and body only. */
    seed: string;
    /** What arrives already built. Three at most — a list that scrolls is a
     *  spec sheet, and nobody hires from a spec sheet. */
    brings: string[];
    /** Written onto the pod as its description. */
    about: string;

    /** The first things to say to them, in your words rather than theirs.
     *
     *  A teammate made a minute ago opens onto an empty thread, and an empty
     *  thread asks a question nobody has an answer to at that moment: what do
     *  you say to something that has not done anything yet? These are three
     *  answers. The reveal puts one in the composer and stops there — a first
     *  message carries the person's name, so it is theirs to send or delete,
     *  the same rule `thread/compose-bridge.ts` keeps for widgets. */
    openers: string[];

    /* The rest is what the candidate's own page shows. A listing is read on
       the same page a hired teammate is read on, so it has to answer the same
       questions — what may it do, what runs without being asked, what did it
       arrive with. `hireFromCard` fills them from the template's card. */
    skills: Skill[];
    permits: string[];
    commitments: { title: string; detail: string; cadence: string }[];
    projects: { name: string; description: string }[];
    counts: { tables: number; functions: number; workflows: number };

    /** The template the backend ships for this role
     *  (`pod_bundle/templates/<bundle>/`), imported when the role is hired.
     *  Absent only on the blank teammate. */
    bundle?: string;
    /** First things to hand the new teammate, with the place each needs. A
     *  win that needs a place says so on its card and connects it there; it
     *  never pretends the place is already connected. `openers` is the same
     *  list's words. */
    wins?: { say: string; needs?: { connector: string; label: string } }[];
    /** Standing work the role offers to take on, turned on with one tap
     *  after the hire rather than created by it: nothing runs on its own
     *  until a person has said so. */
    offers?: { title: string; detail: string; cron: string; instruction: string }[];
    /** What the role is judged on: the measures its template turns on. */
    judgedOn?: string[];
    /** The skills its template ships, by their own names. */
    taught?: { name: string; description: string }[];
    /** The tables its work is kept in. */
    tables?: { name: string; description: string }[];
}

/** A listing, in the shape the profile page reads.
 *
 *  Dates are the honest gap: a candidate has no tenure and nothing has fired
 *  yet, so `joined`, `since` and `last` stay empty and the page says
 *  "available now" where it would otherwise say how long they have been at
 *  it. Everything else is the same field the hired version fills. */
export function profileFor(hire: Hire): Profile {
    return {
        podId: hire.id,
        name: hire.name,
        iconUrl: null,
        headline: hire.role,
        joined: "",
        about: hire.about,
        skills: hire.skills,
        permits: hire.permits,
        commitments: hire.commitments.map((item, index) => ({
            id: hire.id + "-" + index,
            title: item.title,
            detail: item.detail,
            cadence: item.cadence,
            since: "",
            active: false,
            last: "",
        })),
        projects: hire.projects.map((project, index) => ({
            id: hire.id + "-app-" + index,
            name: project.name,
            description: project.description,
            status: "suggested",
            tabId: "",
        })),
        counts: { tables: 0, functions: 0, workflows: 0 },
    };
}

/** Not a fallback: a teammate that starts empty is the one the product's own
 *  line is about — "grows into it". Some jobs have no shelf entry, and the
 *  honest answer is somebody new. It is reached two ways from the shelf — a
 *  described job, or "just exploring" — and is never a card of its own. */
export const BLANK: Hire = {
    id: "blank",
    name: "",
    role: "Learns your work, starting with a responsibility",
    seed: "arch/blank/4",
    brings: [
        "A name and one line about the job",
        "Everything else, as you go",
    ],
    about: "",
    openers: [],
    skills: [],
    permits: [],
    commitments: [],
    projects: [],
    counts: { tables: 0, functions: 0, workflows: 0 },
};

/** A blank hire with a face of its own.
 *
 *  `BLANK.seed` is one fixed string, so taking `BLANK` as it stands gave every
 *  teammate started from a described job the same character — the seed picks
 *  it, and `hire()` then pins it onto the pod on purpose. An archetype wants
 *  that: every Follow-ups looks like Follow-ups. Somebody new is nobody in
 *  particular, so each one is dealt a seed of its own when it is picked, and
 *  the making and the reveal both draw from that same seed. */
export function blankHire(
    nonce: string = Date.now().toString(36) + Math.random().toString(36).slice(2),
): Hire {
    return { ...BLANK, seed: "blank/" + nonce };
}

/** A name for somebody hired without one: their character's.
 *
 *  "Just exploring" hires in one click, so nobody is asked to name a thing
 *  they have not met. "Untitled" would undercut the reveal it lands on, and a
 *  list of first names would be a second identity beside the face. The face
 *  already has a name — the sculpture is Kite, or Bloom — so the name and the
 *  face are dealt together and cannot disagree. Renaming is on the profile. */
export function dealtName(hire: Hire): string {
    const character = characterForSeed(hire.seed);
    return character[0].toUpperCase() + character.slice(1);
}

/** The first things somebody says when they hired without a job in mind.
 *
 *  Questions to the teammate, not a job invented on the person's behalf: they
 *  came to look around, so the openers are ways of looking. The middle one is
 *  a sentence to finish — it opens as a draft, and the week is theirs to
 *  describe. */
export const EXPLORING_OPENERS = [
    "What could you take off my plate?",
    "Here is what a normal week looks like for me:",
    "Show me something you could set up for me today.",
];

/** What to offer as a first message, once the hire is actually made.
 *
 *  A blank teammate has no script and should not be given an invented one —
 *  the app was told the job thirty seconds ago and reciting it back as if it
 *  were advice is a shell game. So it hands the sentence straight back as the
 *  first instruction, which is what the person was going to type anyway. With
 *  no job described, it offers ways to explore instead of an empty section. */
export function openersFor(hire: Hire, job: string): string[] {
    if (hire.id !== "blank") return hire.openers;
    const said = job.trim();
    if (!said) return EXPLORING_OPENERS;
    return [said[0].toUpperCase() + said.slice(1) + (/[.!?]$/.test(said) ? "" : ".")];
}
