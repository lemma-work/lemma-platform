import { key, type KeyValueStore } from "@/session/storage";

/** What was typed and not sent, per conversation, kept across switching away.
 *
 *  The composer's draft was component state, and the composer remounts with
 *  every conversation opened — so a half-written message was gone the moment
 *  someone glanced at another thread. Now it waits in this browser for them to
 *  come back.
 *
 *  One storage entry holding all of them rather than one each, so the sign-in
 *  boundary can drop every draft by name when the person at the browser
 *  changes (`retainWorkspaceOwner`): a draft is somebody's unsent words. Capped
 *  so drafts abandoned for good cannot pile up. */

export const DRAFTS = "drafts";
const KEPT = 40;
const LONGEST = 20_000;

type Drafts = Record<string, string>;

function readAll(store: KeyValueStore): Drafts {
    try {
        const raw = store.getItem(key(DRAFTS));
        const parsed: unknown = raw ? JSON.parse(raw) : {};
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
        const drafts: Drafts = {};
        for (const [name, text] of Object.entries(parsed)) if (typeof text === "string") drafts[name] = text;
        return drafts;
    } catch {
        return {};
    }
}

function writeAll(store: KeyValueStore, drafts: Drafts): void {
    try {
        const names = Object.keys(drafts);
        if (names.length === 0) store.removeItem(key(DRAFTS));
        else store.setItem(key(DRAFTS), JSON.stringify(drafts));
    } catch {
        /* Storage full or unavailable: the draft lives as long as the pane. */
    }
}

export function readDraft(store: KeyValueStore, draftKey: string): string {
    return readAll(store)[draftKey] ?? "";
}

/** Saves `text` as this conversation's draft; an empty one removes it. Most
 *  recently written last, so the cap drops the oldest. */
export function writeDraft(store: KeyValueStore, draftKey: string, text: string): void {
    const drafts = readAll(store);
    const kept = text.trim() ? text.slice(0, LONGEST) : "";
    if ((drafts[draftKey] ?? "") === kept) return;
    delete drafts[draftKey];
    if (kept) drafts[draftKey] = kept;
    const names = Object.keys(drafts);
    for (const name of names.slice(0, Math.max(0, names.length - KEPT))) delete drafts[name];
    writeAll(store, drafts);
}

/** Which box a draft belongs to.
 *
 *  A pane showing a conversation keys by it, so two conversations with the same
 *  teammate keep two drafts and neither is offered the other's words. A pane
 *  whose conversation does not exist yet — the chat beside a doc, before
 *  anything has been sent — keys by what that pane is about, so its words wait
 *  for it without following the person to the next doc. A pane with neither
 *  keeps the one draft a new conversation in the main pane has always had. */
export function draftKeyFor(podId: string, conversationId: string | null, scope?: string | null): string {
    if (conversationId) return podId + ":" + conversationId;
    return scope ? podId + ":new:" + scope : podId + ":new";
}

/** The browser's storage, or nothing — a private window can refuse it, and a
 *  draft is not worth an error. */
export function draftStore(): KeyValueStore | null {
    try {
        return typeof window === "undefined" ? null : window.localStorage;
    } catch {
        return null;
    }
}
