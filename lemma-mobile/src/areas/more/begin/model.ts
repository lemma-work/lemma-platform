import { routes } from "../../../data/routes";
import { adminRoutes } from "../../admin/routes";
import type { JoinRequest } from "@web/data/joining";
import { isMissing, statusOf } from "../../admin/words";
import { sharedLinkPath } from "@web/outcomes/model";
import { appPathOf } from "../links";
import { CHARACTERS } from "@web/shell/cast";
import type { StartedLemma } from "./source";

/** Starting a Lemma, and asking to join one: the phone's own reading on top
 *  of the web's (`outcomes/start-lemma.tsx`). Pure, so `tests/` can hold it. */

/* ── a face ──────────────────────────────────────────────────────── */

/** A template's face as the web stores it on the Lemma it starts (its
 *  `characterUrl`), so the face picked on the shelf is the face it wears.
 *  Null for a face this app does not know. */
export function castIcon(face: string | null): string | null {
    return face && (CHARACTERS as readonly string[]).includes(face) ? `/teammates/cast/${face}.webp` : null;
}

/** Where a pasted link to something shared opens on the phone: a GitHub
 *  repository's import, or a share, remix or import link of one of
 *  `origins` at the phone's own screen for it, else on the web. Null for
 *  anything else. */
export function sharedOpen(text: string, origins: readonly string[]): string | null {
    const path = sharedLinkPath(text, origins);
    if (!path || !path.startsWith("/") || path.startsWith("//")) return null;
    return appPathOf(path) ?? routes.web("", path, "Shared with you");
}

/* ── starting one ───────────────────────────────────────────────────── */

export function takenSentence(name: string): string {
    return `There’s already a Lemma called ${name.trim()} here. Choose another name.`;
}

/** Whether the server refused a name as taken: a conflict, or its words for
 *  one ("… already exists in this organization"), which name a pod. */
export function isNameConflict(error: unknown): boolean {
    if (statusOf(error) === 409) return true;
    return error instanceof Error && /already exists/i.test(error.message);
}

/** Where a start lands: its own page. Sample data cannot add a Lemma, so
 *  its stand-in's page opens instead. */
export function startedLanding(started: StartedLemma): string {
    return routes.lemma(started.standsIn?.id ?? started.id);
}

/** Where an ending's ways on go. */
export const nextFor = {
    models: () => adminRoutes.section("models"),
};

/** Said when sample data's fixed list of Lemmas could not take a new one. */
export function standInWords(name: string, standIn: string): string {
    return `Sample data has a fixed set of Lemmas, so nothing was added. ${standIn} stands in for ${name}: its chat opens from here.`;
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
