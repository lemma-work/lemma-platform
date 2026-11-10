import { SAMPLE_LEMMAS } from "../../../data/sample";
import type { JoinRequest } from "@web/data/joining";
import type { ShelfCard } from "@web/outcomes/model";
import type { BeginSource, SetupStatus } from "./source";

/** Starting a Lemma and asking to join one, as sample data. The shelf is
 *  the templates the server ships; a start really runs, in memory and at a
 *  pace that can be watched, and its template comes in step by step; but
 *  the list of Lemmas is fixed (`data/sample.ts`), so nothing is added to
 *  it. An existing Lemma stands in instead (`standsIn`), and the screens say
 *  so: one of the same name, else the nearest in kind. Whether the
 *  organization has a model alternates from one start to the next, so both
 *  endings can be seen.
 *
 *  Asking to join, `lemma://join/<id>`: `sample-closed` (outside it: Ask to
 *  join, which then waits), `sample-waiting` (asked already),
 *  `sample-open-door` (asking lets you in on the spot, though the fixed list
 *  never shows it), `sample-missing` (no such Lemma), and any of the sample
 *  Lemmas' own ids (already let in, and it opens). */

/** The templates the server ships (`pod_bundle/templates`), as it lists them. */
export const SAMPLE_SHELF: ShelfCard[] = [
    { id: "support", name: "Support", sentence: "Every customer hears back within a day.", kind: "week", work: "support", face: "bloom", place: "Intercom", brings: ["A table of every customer waiting", "Drafts for anyone waiting, twice a day", "The week’s number in Pages, every Friday"], never: ["Send anything to a customer without a person checking it first"] },
    { id: "follow-ups", name: "Follow-ups", sentence: "Every lead gets a follow-up within two days.", kind: "week", work: "sales", face: "loop", place: "HubSpot", brings: ["A table of every lead and when they were followed up", "Drafts for quiet leads, every weekday", "The week’s number in Pages, every Friday"], never: ["Send a follow-up its owner has not approved"] },
    { id: "partners", name: "Partners", sentence: "20 design partners signed by {due}.", kind: "date", work: "sales", face: "peak", place: "HubSpot", brings: ["A table of every company you are talking to", "A chase for anything quiet five days", "Who signed, and who is close, every Friday"], never: ["Count a partner as signed before the agreement is"] },
    { id: "reviews", name: "Reviews", sentence: "Every pull request is reviewed the same day.", kind: "week", work: "engineering", face: "cube", place: "GitHub", brings: ["A table of open pull requests and their reviewers", "A nudge for anything open four hours", "The week’s number in Pages, every Friday"], never: ["Approve or merge a pull request"] },
    { id: "launch", name: "Launch", sentence: "The launch ships on {due}.", kind: "date", work: "engineering", face: "spark", place: "Linear", brings: ["A checklist of every step, with owners and dates", "The week’s plan, every Monday", "A chase for anything due within three days"], never: ["Mark a step done that is not done"] },
    { id: "content", name: "Content", sentence: "Two posts go out every week.", kind: "week", work: "marketing", face: "wave", place: "Notion", brings: ["A table of every post, drafted and published", "A draft every Tuesday and Thursday", "The week’s count in Pages, every Friday"], never: ["Publish anything yourself"] },
    { id: "audit", name: "Audit", sentence: "The SOC 2 audit passes by {due}.", kind: "date", work: "ops", face: "ring", place: "Notion", brings: ["A table of every control and its evidence", "A check that evidence is still fresh, every Monday", "A chase for controls with no owner"], never: ["Mark a control ready without evidence from the last 90 days"] },
];

/** What a sample template brings in, in the order an import's steps come. */
const BROUGHT = [
    { kind: "TABLE", name: "conversations" },
    { kind: "FILE", name: "skills/set-up/SKILL.md" },
    { kind: "FILE", name: "skills/keep-up/SKILL.md" },
    { kind: "SCHEDULE", name: "twice_a_day" },
    { kind: "SCHEDULE", name: "friday_number" },
];

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** The nearest sample Lemma for a start that is not one of them. */
const NEAREST: Record<string, string> = {
    reviews: "Launch",
    "follow-ups": "Renewals",
    partners: "Renewals",
    content: "Launch",
    audit: "Books",
};

function standInFor(name: string) {
    const said = name.trim().toLowerCase();
    const named = SAMPLE_LEMMAS.find((one) => one.name.toLowerCase() === said);
    const nearest = SAMPLE_LEMMAS.find((one) => one.name === NEAREST[said]);
    return named ?? nearest ?? SAMPLE_LEMMAS[0];
}

function request(podId: string, standing: JoinRequest["standing"]): JoinRequest {
    return { id: `sample-join-${podId}`, podId, standing, name: "Ada Park", email: "ada@acme.example", askedAt: new Date().toISOString() };
}

export function beginSample(): BeginSource {
    const asked = new Map<string, JoinRequest>([["sample-waiting", request("sample-waiting", "pending")]]);
    /* Each import's first read, so its steps come in over a few seconds. */
    const begun = new Map<string, number>();
    let starts = 0;

    return {
        async templates() {
            await wait(400);
            return SAMPLE_SHELF;
        },
        async start(request) {
            await wait(1100);
            const card = "template" in request ? SAMPLE_SHELF.find((one) => one.id === request.template) : null;
            const name = card?.name ?? ("name" in request ? request.name : "Lemma");
            const standIn = standInFor(name);
            return { id: standIn.id, name, conversationId: "new", importId: card ? `sample-import-${card.id}` : null, standsIn: { id: standIn.id, name: standIn.name } };
        },
        async setFace() {
            await wait(300);
        },
        async setup(_podId, importId): Promise<SetupStatus> {
            await wait(200);
            const first = begun.get(importId) ?? Date.now();
            begun.set(importId, first);
            const shown = Math.min(BROUGHT.length, 1 + Math.floor((Date.now() - first) / 1200));
            return { status: shown < BROUGHT.length ? "RUNNING" : "COMPLETED", steps: BROUGHT.slice(0, shown), error: null };
        },
        async needsModel() {
            await wait(200);
            starts += 1;
            return starts % 2 === 0;
        },
        async homeOf(podId) {
            return SAMPLE_LEMMAS.some((one) => one.id === podId) ? "sample-acme" : null;
        },
        async myJoinRequest(podId) {
            await wait(300);
            if (SAMPLE_LEMMAS.some((one) => one.id === podId)) return request(podId, "approved");
            return asked.get(podId) ?? null;
        },
        async askToJoin(podId) {
            await wait(700);
            if (podId === "sample-missing") throw Object.assign(new Error("Not found"), { statusCode: 404 });
            const made = request(podId, podId === "sample-open-door" ? "approved" : "pending");
            asked.set(podId, made);
            return made;
        },
    };
}
