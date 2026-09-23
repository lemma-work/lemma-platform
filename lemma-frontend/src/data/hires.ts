import type { Profile, Skill } from "./types";

/** Who is on the shelf.
 *
 *  **This file is the placeholder.** Every entry here is meant to become a
 *  published pod bundle — the backend already has the whole surface for it
 *  (`pod_bundle`: export, publish, import), so hiring one becomes an import
 *  rather than an empty `pods.create`. Until those bundles exist, the shelf
 *  runs on this list so the flow can be walked and judged.
 *
 *  When the real ones land, the shape below is what a listing has to expose:
 *  a name, one honest line about the job, the archetype face, and — the part
 *  that does the persuading — what actually arrives with them. A listing that
 *  cannot say "a table, a schedule and an app come with this" is a prompt
 *  wrapper, and a shelf of those is how the GPT store died.
 *
 *  `seed` is the archetype's face. It is not stored on the pod: it picks the
 *  character, and the hire's own id is then searched for a variant landing on
 *  the same one. See `variantForCharacter` — your Follow-ups and mine are the
 *  same role and the same character, on two different pods.
 *
 *  The seeds are searched, not typed. Six arbitrary ones gave six versions of
 *  the same round body in six colours, which is the failure this whole shelf
 *  is supposed to avoid: at the size a card draws them, silhouette separates
 *  two candidates and hue does not.
 *
 *  They were searched again when the face stopped being a generated creature
 *  and became one of twenty-four sculptures: a different hash, so the old
 *  numbers no longer held and two of the six came out as Stack. Re-run the
 *  search against `characterForSeed` if that hash or the cast ever changes —
 *  nothing here fails loudly when a shelf goes back to showing duplicates.
 *  The characters are also chosen rather than merely distinct: Moon keeps the
 *  night shift, Grid keeps the ledger, Bloom is glad you are here. */
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
       arrive with. A bundle manifest supplies exactly these; until then they
       are written here. */
    skills: Skill[];
    permits: string[];
    commitments: { title: string; detail: string; cadence: string }[];
    projects: { name: string; description: string }[];
    counts: { tables: number; functions: number; workflows: number };

    /** The bundle this becomes. Empty until one is published. */
    bundle?: string;
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

export const HIRES: Hire[] = [
    {
        id: "follow-ups",
        name: "Follow-ups",
        role: "Keeps track of commitments and prepares follow-ups",
        seed: "arch/follow-ups/6",
        brings: [
            "A shared list of commitments",
            "A weekday follow-up check",
            "Drafts for you to review",
        ],
        about:
            "Keeps track of the commitments this team shares with it. " +
            "Reads replies, closes what landed, and raises what has gone cold. Drafts the " +
            "follow-up but never sends it without a person saying so.",
        openers: [
            "Here is everything I said I would do this week.",
            "What have I promised someone that has gone quiet?",
            "Sweep every weekday morning and tell me what has gone cold.",
        ],
        skills: [
            { id: "MEMORY", label: "Memory", blurb: "Knows what was promised three weeks ago" },
            { id: "MESSAGING", label: "Reaches people", blurb: "Sends the nudge to the person, not the channel" },
            { id: "TODO", label: "Keeps a plan", blurb: "One row per promise, and it closes them" },
            { id: "USER_INTERACTION", label: "Asks first", blurb: "Drafts, then waits for your yes" },
        ],
        permits: ["tables.write", "messages.draft"],
        commitments: [
            { title: "Morning sweep", detail: "Re-reads every open loop and raises what has gone quiet", cadence: "Every weekday at 09:00" },
            { title: "Reply watch", detail: "Checks for replies and closes resolved follow-ups", cadence: "When something arrives" },
        ],
        projects: [{ name: "Open loops", description: "Commitments, owners and follow-up dates" }],
        counts: { tables: 1, functions: 2, workflows: 1 },
    },
    {
        id: "research",
        name: "Research",
        role: "Reads your sources and helps you make sense of them",
        seed: "arch/research/13",
        brings: [
            "A library of links, PDFs and notes",
            "Answers with sources attached",
            "A weekly digest of changes",
        ],
        about:
            "Turns the documents, links and notes this team collects into answers. Always " +
            "cites what it read. Says plainly when the sources disagree, and says nothing " +
            "when it does not know.",
        openers: [
            "Here is a link. Read it and tell me what actually matters.",
            "Watch these sources and tell me each week what changed.",
            "Whenever you answer me, attach what you read it in.",
        ],
        skills: [
            { id: "WEB_SEARCH", label: "Research", blurb: "Searches and reads the open web" },
            { id: "SKILLS", label: "Reads what you save", blurb: "Documents, links and notes, all of it" },
            { id: "MEMORY", label: "Memory", blurb: "Remembers what it already told you" },
        ],
        permits: ["files.read", "tables.write"],
        commitments: [
            { title: "Weekly digest", detail: "What changed in the sources this team watches", cadence: "Every Monday at 08:00" },
        ],
        projects: [{ name: "Library", description: "Saved sources and answers" }],
        counts: { tables: 1, functions: 1, workflows: 0 },
    },
    {
        id: "reception",
        name: "Reception",
        role: "Handles incoming questions and flags what needs you",
        seed: "arch/reception/13",
        brings: [
            "Connected channels for incoming questions",
            "Guidance on what to answer and when to ask",
            "A record of replies for your team to review",
        ],
        about:
            "The first reply. Handles the questions with known answers, hands over anything " +
            "that needs a person, and never invents a commitment on the team's behalf.",
        openers: [
            "Take tonight's inbox. I will read what you could not answer.",
            "Here is how to answer the three questions we get most.",
            "Anything you are not sure about, hand straight to me.",
        ],
        skills: [
            { id: "MESSAGING", label: "Reaches people", blurb: "Answers on the channel the question came in on" },
            { id: "USER_INTERACTION", label: "Asks first", blurb: "Hands over anything it should not decide" },
            { id: "MEMORY", label: "Memory", blurb: "Recognises somebody who wrote last week" },
        ],
        permits: ["messages.send", "tables.write"],
        commitments: [
            { title: "First reply", detail: "Drafts answers and flags questions that need a person", cadence: "When something arrives" },
        ],
        projects: [{ name: "Front desk log", description: "Everything said in your name, readable" }],
        counts: { tables: 2, functions: 1, workflows: 1 },
    },
    {
        id: "night-shift",
        name: "Night shift",
        role: "Runs the 2am job so nobody sets an alarm",
        seed: "arch/night-shift/18",
        brings: [
            "A schedule for the recurring job",
            "Retry limits and failure notifications",
            "A morning report of results",
        ],
        about:
            "Owns the recurring work that happens outside office hours. Runs it, checks the " +
            "result, and leaves a short account of what happened for whoever opens the " +
            "laptop first.",
        openers: [
            "Run this every night at two and leave me the account by seven.",
            "Here is the job. Retry it twice, then tell me you gave up.",
            "What would you need from me to run this unattended?",
        ],
        skills: [
            { id: "WORKSPACE_CLI", label: "Works a computer", blurb: "A shell, a filesystem and a browser of its own" },
            { id: "SNOOZE", label: "Patience", blurb: "Puts work down and picks it up on time" },
            { id: "TODO", label: "Keeps a plan", blurb: "Knows what it owes tonight" },
        ],
        permits: ["functions.run", "files.write"],
        commitments: [
            { title: "The nightly run", detail: "Run a defined task at the time you choose", cadence: "Every day at 02:00" },
            { title: "Morning report", detail: "What ran, what failed, what it gave up on", cadence: "Every day at 07:30" },
        ],
        projects: [],
        counts: { tables: 1, functions: 3, workflows: 2 },
    },
    {
        id: "launches",
        name: "Launches",
        role: "Holds the checklist and chases each owner",
        seed: "arch/launches/5",
        brings: [
            "A plan with an owner against every line",
            "Reminders for the people responsible",
            "A shared view of progress and blockers",
        ],
        about:
            "Keeps a launch honest. Tracks what is done, what is blocked and who owes what, " +
            "and asks the person directly rather than broadcasting into a channel.",
        openers: [
            "Here is the plan. Put an owner against every line.",
            "Chase whoever is late directly, not in the channel.",
            "Where are we? One paragraph, no list.",
        ],
        skills: [
            { id: "TODO", label: "Keeps a plan", blurb: "An owner against every line" },
            { id: "MESSAGING", label: "Reaches people", blurb: "Follows up with the person responsible" },
            { id: "SUBAGENTS", label: "Delegation", blurb: "Hands the work out and waits on it" },
        ],
        permits: ["tables.write", "messages.draft"],
        commitments: [
            { title: "Standup sweep", detail: "Asks each owner what moved, and what did not", cadence: "Every weekday at 10:00" },
        ],
        projects: [{ name: "Launch board", description: "Status anyone can read without asking" }],
        counts: { tables: 2, functions: 1, workflows: 1 },
    },
    {
        id: "ledger",
        name: "Ledger",
        role: "Reconciles the numbers and flags mismatches",
        seed: "arch/ledger/31",
        brings: [
            "Source tables and a nightly reconciliation",
            "A list of mismatched records",
            "A review step before changing numbers",
        ],
        about:
            "Pulls the numbers together from wherever they live, matches them, and raises " +
            "the ones that do not agree. Proposes the correction; a person approves it.",
        openers: [
            "Here are the two sources. Tell me what will not tie.",
            "Reconcile these every night and raise the breaks.",
            "Never change a number without asking me first.",
        ],
        skills: [
            { id: "CONNECTORS", label: "Connected accounts", blurb: "Reads the numbers where they already live" },
            { id: "USER_INTERACTION", label: "Asks first", blurb: "Never changes a figure on its own" },
            { id: "MEMORY", label: "Memory", blurb: "Knows what last month tied to" },
        ],
        permits: ["tables.write", "connectors.read"],
        commitments: [
            { title: "Nightly reconcile", detail: "Matches every source and flags the rows that disagree", cadence: "Every day at 01:00" },
        ],
        projects: [{ name: "Breaks", description: "What will not tie, and by how much" }],
        counts: { tables: 3, functions: 2, workflows: 1 },
    },
];

/** The last card, and not a fallback: a teammate that starts empty is the one
 *  the product's own line is about — "grows into it". Some jobs have no shelf
 *  entry, and the honest answer is somebody new. */
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

/** What to offer as a first message, once the hire is actually made.
 *
 *  A blank teammate has no script and should not be given an invented one —
 *  the app was told the job thirty seconds ago and reciting it back as if it
 *  were advice is a shell game. So it hands the sentence straight back as the
 *  first instruction, which is what the person was going to type anyway, and
 *  offers nothing at all when they did not describe the job. */
export function openersFor(hire: Hire, job: string): string[] {
    if (hire.id !== "blank") return hire.openers;
    const said = job.trim();
    if (!said) return [];
    return [said[0].toUpperCase() + said.slice(1) + (/[.!?]$/.test(said) ? "" : ".")];
}
