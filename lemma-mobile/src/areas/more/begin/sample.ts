import { SAMPLE_LEMMAS } from "../../../data/sample";
import type { JoinRequest } from "@web/data/joining";
import type { BeginSource } from "./source";

/** Starting a Lemma and asking to join one, as sample data. Every step of a
 *  start really runs, in memory and at a pace that can be watched; but the
 *  list of Lemmas is fixed (`data/sample.ts`), so nothing is added to it.
 *  An existing Lemma stands in instead (`standsIn`), and the screens say so:
 *  one of the same name, else the nearest in kind. Whether the organization
 *  has a model alternates from one start to the next, so both endings can be
 *  seen.
 *
 *  Asking to join, `lemma://join/<id>`: `sample-closed` (outside it: Ask to
 *  join, which then waits), `sample-waiting` (asked already),
 *  `sample-open-door` (asking lets you in on the spot, though the fixed list
 *  never shows it), `sample-missing` (no such Lemma), and any of the sample
 *  Lemmas' own ids (already let in, and it opens). */

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** The nearest sample Lemma for a start that is not one of them. */
const NEAREST: Record<string, string> = {
    reviews: "Launch",
    onboarding: "Support",
    close: "Books",
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
    const plans = new Map<string, string>();
    const asked = new Map<string, JoinRequest>([["sample-waiting", request("sample-waiting", "pending")]]);
    let starts = 0;

    return {
        async create(_orgId, name) {
            await wait(900);
            const standIn = standInFor(name);
            return { id: standIn.id, name, standsIn: { id: standIn.id, name: standIn.name } };
        },
        async setFace() {
            await wait(600);
        },
        async writePlan(podId, text) {
            await wait(700);
            plans.set(podId, text);
        },
        async askToSetUp() {
            await wait(800);
            return "new";
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
