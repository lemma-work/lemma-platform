import { routes } from "../../../data/routes";
import { peopleRoutes } from "../../admin/people/routes";
import { adminRoutes } from "../../admin/routes";
import type { JoinRequest } from "@web/data/joining";
import { isMissing, statusOf } from "../../admin/words";
import { chatRoutes } from "../../chat/routes";
import { emptyPlan, planRequest, serializePlan, type Plan } from "../../work/web/plan";
import { characterForSeed, type CharacterName } from "@web/shell/cast";
import { BLANK, blankHire, openersFor } from "@web/data/hires";

/** Starting a Lemma, and asking to join one: the phone's own reading on top
 *  of the web's `web/hires.ts`, `web/cast.ts` and the `/o` frame's new-Lemma
 *  list. Pure, so `tests/` can hold it. */

/* ── what a new Lemma could own ─────────────────────────────────────── */

/** A starter, as the `/o` frame's new-Lemma list has it
 *  (`lemma-frontend/src/outcomes/list.tsx` on branch
 *  `lemma/single-pod-exposure-8144fd`, which is not on main, so no test
 *  holds these to it: when it lands, copy them across again). Each says
 *  what it would count and build; none claims a result. */
export interface Starter {
    name: string;
    outcome: string;
    team: string;
    kind: "Every week" | "By a date" | "Every month";
    counts: string;
    builds: string;
    works: string;
}

export const STARTERS: readonly Starter[] = [
    { name: "Reviews", outcome: "Every pull request is reviewed the same day", team: "Engineering", kind: "Every week", counts: "Pull requests reviewed within 8 working hours, from GitHub", builds: "A review queue app, an hourly nudge, a Friday report", works: "GitHub, Slack" },
    { name: "Renewals", outcome: "Every renewal is confirmed 30 days early", team: "Sales", kind: "Every week", counts: "Renewals confirmed before the 30-day mark, from your CRM", builds: "A renewals table, a call list, reminders to account owners", works: "HubSpot, Gmail" },
    { name: "Onboarding", outcome: "Every new customer is live within 14 days", team: "Customers", kind: "Every week", counts: "Customers live within 14 days of signing", builds: "An onboarding tracker, a checklist per customer, nudges", works: "Intercom, Slack" },
    { name: "Support", outcome: "Every customer hears back within a day", team: "Customers", kind: "Every week", counts: "Conversations answered within 24 hours", builds: "A reply queue, drafts for anything waiting, a Friday report", works: "Intercom, Gmail" },
    { name: "Close", outcome: "The books close by the 5th of each month", team: "Finance", kind: "Every month", counts: "Close checklist done by day 5", builds: "A close checklist, a reconciliation table, chases for receipts", works: "Xero, Gmail" },
    { name: "Hiring", outcome: "Two engineers signed by a date you choose", team: "People", kind: "By a date", counts: "Signed offers", builds: "A candidate pipeline, interview booking, scorecard chases", works: "Ashby, Google Calendar" },
];

/** The plan's kind for a starter (the `/o` frame's `kindOfStarter`): a plan
 *  holds a weekly bar or a date, and says neither for a monthly one rather
 *  than claim a cadence it is not. */
export function kindOfStarter(starter: Pick<Starter, "kind">): Plan["kind"] {
    return starter.kind === "Every week" ? "week" : starter.kind === "By a date" ? "date" : null;
}

/** "Owns: every pull request is reviewed the same day." */
export function ownsLine(outcome: string): string {
    const said = outcome.trim().replace(/[.!?]+$/, "");
    return said ? `Owns: ${said.charAt(0).toLowerCase()}${said.slice(1)}.` : "";
}

/** A name not already taken here (the `/o` frame's `freeName`): "Support",
 *  else "Support 2", and so on. `taken` holds lowercased names. */
export function freeName(wanted: string, taken: ReadonlySet<string>): string {
    if (!taken.has(wanted.toLowerCase())) return wanted;
    for (let index = 2; index < 50; index += 1) {
        const candidate = `${wanted} ${index}`;
        if (!taken.has(candidate.toLowerCase())) return candidate;
    }
    return `${wanted} ${Date.now() % 1000}`;
}

/** A name for an outcome somebody said in their own words (the `/o`
 *  frame's `ownName`): its first long word that is not a filler. */
export function ownName(text: string): string {
    const words = text.replace(/[^A-Za-z0-9 ]+/g, " ").trim().split(/\s+/).filter(Boolean);
    const pick = words.find((word) => word.length > 4 && !/^(every|within|before|after|their|there)$/i.test(word)) ?? words[0] ?? "Lemma";
    return pick.charAt(0).toUpperCase() + pick.slice(1).toLowerCase();
}

/** The plan written for a new Lemma: the outcome, its kind, and the outcome
 *  again as the first thing that must be true (the `/o` frame's start). */
export function startPlan(outcome: string, kind: Plan["kind"]): string {
    const plan: Plan = { ...emptyPlan(outcome), kind, sections: { must: [{ text: outcome }], how: [], who: [], never: [] } };
    return serializePlan(plan);
}

/** What the new Lemma is asked in its first chat (the `/o` frame's words). */
export function setupAsk(outcome: string): string {
    const request = planRequest(outcome);
    return `Set yourself up to own this: "${outcome}". Look at what is already connected, build the tables, app and schedules it needs, then ${request.charAt(0).toLowerCase()}${request.slice(1)}`;
}

/* ── starting one ───────────────────────────────────────────────────── */

/** What is being started: something to own, or only a name (the web's
 *  blank start). `seed` picks its face before it exists; the Lemma's own id
 *  then finds the variant that draws the same one. */
export type Start =
    | { kind: "own"; name: string; outcome: string; plan: Plan["kind"]; seed: string }
    | { kind: "blank"; name: string; job: string; seed: string };

export function ownStart(name: string, outcome: string, plan: Plan["kind"]): Start {
    return { kind: "own", name, outcome: outcome.trim(), plan, seed: `owns/${name.toLowerCase()}` };
}

/** Somebody new is nobody in particular, so each blank start is dealt a
 *  face of its own (`blankHire`). */
export function blankStart(name: string, job: string, nonce?: string): Start {
    return { kind: "blank", name: name.trim(), job: job.trim(), seed: blankHire(nonce).seed };
}

/** The character a start's face is drawn as. */
export function characterOf(start: Pick<Start, "seed">): CharacterName {
    return characterForSeed(start.seed);
}

/** The same start, asked again: a retry finishes the Lemma it already made
 *  rather than making a second one. */
export function sameStart(a: Start | null, b: Start): boolean {
    if (!a || a.kind !== b.kind || a.name !== b.name || a.seed !== b.seed) return false;
    return a.kind === "own" && b.kind === "own" ? a.outcome === b.outcome : a.kind === "blank" && b.kind === "blank" && a.job === b.job;
}

export type StepId = "made" | "face" | "plan" | "ask";

/** Only the steps that really happen: the Lemma, its face, and for one that
 *  owns something, its plan and the chat where it sets itself up. */
export function stepsFor(start: Start): { id: StepId; label: string }[] {
    const made = [
        { id: "made" as const, label: `Starting ${start.name}` },
        { id: "face" as const, label: "Giving it a face" },
    ];
    if (start.kind === "blank") return made;
    return [...made, { id: "plan", label: "Writing what it owns into its plan" }, { id: "ask", label: "Asking it to set itself up" }];
}

/** A step as drawn: ticked once it really finished, turning while it runs,
 *  stopped where a failure left it, waiting otherwise. */
export function stepState(id: StepId, steps: readonly { id: StepId }[], done: readonly StepId[], failed: boolean): "done" | "running" | "stopped" | "waiting" {
    if (done.includes(id)) return "done";
    const next = steps.find((step) => !done.includes(step.id));
    if (next?.id !== id) return "waiting";
    return failed ? "stopped" : "running";
}

/** A Lemma's name is one per organization. */
export function nameTaken(name: string, taken: readonly string[]): boolean {
    const key = name.trim().toLowerCase();
    return Boolean(key) && taken.some((one) => one.trim().toLowerCase() === key);
}

export function takenSentence(name: string): string {
    return `There’s already a Lemma called ${name.trim()} here. Choose another name.`;
}

/** Whether the server refused a name as taken: a conflict, or its words for
 *  one ("… already exists in this organization"), which name a pod. */
export function isNameConflict(error: unknown): boolean {
    if (statusOf(error) === 409) return true;
    return error instanceof Error && /already exists/i.test(error.message);
}

/* ── the blank start's ending ───────────────────────────────────────── */

/** The first things to say, opened as a draft, never sent (the web's
 *  `openersFor` on a blank start): the job handed straight back, or ways of
 *  looking around when no job was said. */
export function firstTasks(job: string): string[] {
    return openersFor(BLANK, job);
}

/** What the ending says. "Ready" is a claim about the next message, and
 *  without a model the next message fails, so it is not made then. */
export function readyWords(name: string, job: string, needsModel: boolean): { heading: string; line: string | null; tasks: string } {
    const exploring = !job.trim();
    return {
        heading: needsModel ? `${name} needs an AI model before it can reply.` : `${name} is ready`,
        line: needsModel ? null : exploring
            ? "It starts with no job yet. Tell it about your work and find one together."
            : "Share what it needs to know for its first task.",
        tasks: exploring ? `Or ask ${name} something` : `Or give ${name} a first task`,
    };
}

/** Where the ending's ways on go. */
export const nextFor = {
    open: (podId: string) => routes.lemma(podId),
    task: (podId: string, words: string) => chatRoutes.filled(podId, words),
    reach: (podId: string) => adminRoutes.lemmaSection(podId, "channels"),
    people: (podId: string) => peopleRoutes.add(podId),
    models: () => adminRoutes.section("models"),
};

/** Where an owned start lands: the chat where it sets itself up, else its
 *  page. Sample data cannot add a Lemma, so its stand-in's chat opens with
 *  the same words in the box, sent by nobody. */
export function ownedLanding(made: { id: string; standsIn?: { id: string } | null }, conversationId: string | null, asked: string): string {
    if (made.standsIn) return chatRoutes.filled(made.standsIn.id, asked);
    return conversationId ? routes.chat(made.id, conversationId) : routes.lemma(made.id);
}

/** Said when sample data's fixed list of Lemmas could not take a new one. */
export function standInWords(name: string, standIn: string, owned: boolean): string {
    return owned
        ? `Sample data has a fixed set of Lemmas, so nothing was added. ${standIn} stands in for ${name}: its chat opens with the setup words in the box, unsent.`
        : `Sample data has a fixed set of Lemmas, so nothing was added. ${standIn} stands in for ${name}: what opens from here is ${standIn}’s.`;
}

/* ── asking to join ─────────────────────────────────────────────────── */

/** Where somebody stands at a Lemma that is not theirs (the web's
 *  `NotYours`): no such Lemma, let in, asked and waiting, or outside it. */
export type JoinState = "missing" | "admitted" | "waiting" | "closed";

export function joinState({ standing, asked, standingError, askError }: {
    standing: JoinRequest | null | undefined;
    asked: JoinRequest | null | undefined;
    standingError?: unknown;
    askError?: unknown;
}): JoinState {
    /* Not a locked door: no door. Offering to ask for something that does
       not exist sends somebody to wait for an answer that cannot come. */
    if (isMissing(askError) || isMissing(standingError)) return "missing";
    const latest = asked ?? standing;
    if (latest?.standing === "approved") return "admitted";
    if (latest?.standing === "pending") return "waiting";
    return "closed";
}

export const JOIN_WORDS: Record<JoinState, { title: string; body: string }> = {
    missing: {
        title: "No such Lemma",
        body: "This link points at a Lemma that doesn’t exist, or one that has since been deleted. Check the link with whoever sent it.",
    },
    admitted: {
        title: "You’re in",
        body: "It was already open to you. Opening it now.",
    },
    waiting: {
        title: "Asked to join",
        body: "Whoever runs this Lemma has your ask, here and by email. You’re added once one of them says yes. Nothing else is needed from you.",
    },
    closed: {
        title: "This Lemma isn’t one of yours",
        body: "Your account can’t open it yet. You can ask to join.",
    },
};
