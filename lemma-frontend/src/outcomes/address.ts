/** The outcome frame's address grammar: which view a `/o` path names, and the
 *  path for a view. Kept apart from `model.ts` and free of imports, so code
 *  that only needs to read an address — analytics templating one, on every
 *  page — does not carry the plan parser and the channel model with it. */

export type OutcomeTab = "home" | "plan" | "apps" | "made" | "contacts" | "xray" | "settings";

/** The id a chat not started yet goes by, in the address and in the data
 *  source (`NEW_CONVERSATION`). Repeated rather than imported so this file
 *  stays free of the data layer. */
export const NEW_CHAT = "new";

/** What the main area has open as a chat. A chat is a conversation with a
 *  Lemma (`new` for one not started yet); a shared chat is a channel:
 *  someone's private line, a group made here, or one mirrored from a
 *  platform. A contact is someone outside the Lemma who wrote in, and their
 *  chat with it; the queue of them is the Lemma's Contacts tab. */
export type ChatOpen =
    | { kind: "none" }
    | { kind: "chat"; podId: string; id: string }
    | { kind: "shared"; podId: string; id: string }
    | { kind: "contact"; podId: string; id: string };

export type OpenChat = Exclude<ChatOpen, { kind: "none" }>;

export const NO_CHAT: ChatOpen = { kind: "none" };

export type View =
    | { name: "today" }
    | { name: "outcomes" }
    | { name: "needs" }
    | { name: "chats"; open: ChatOpen }
    | { name: "people" }
    /** `section` is a part of a Lemma's settings (its About), on that tab. */
    | { name: "outcome"; podId: string; tab: OutcomeTab; section?: string }
    | { name: "run"; podId: string; runId: string }
    | { name: "app"; podId: string; app: string }
    | { name: "file"; podId: string; path: string }
    | { name: "table"; podId: string; table: string }
    | { name: "workflow"; podId: string; workflow: string }
    | { name: "agent"; podId: string; agent: string }
    | { name: "record"; podId: string; table: string; recordId: string }
    /** Starting a Lemma from scratch, the workspace's own way. */
    | { name: "hire" };

export const OUTCOME_TABS: readonly OutcomeTab[] = ["home", "plan", "apps", "made", "contacts", "xray", "settings"];

/** A Lemma's page, on its home unless another tab is named. */
export function outcomeView(podId: string, tab: OutcomeTab = "home", section: string | null = null): View {
    return section && tab === "settings" ? { name: "outcome", podId, tab, section } : { name: "outcome", podId, tab };
}

/** A chat, open in the main area beside the sidebar that lists it. */
export function chatView(open: OpenChat): View {
    return { name: "chats", open };
}

/** The outcome a view is about, when it is about one. */
export function podOfView(view: View): string | null {
    switch (view.name) {
        case "outcome":
        case "run":
        case "app":
        case "file":
        case "table":
        case "workflow":
        case "agent":
        case "record":
            return view.podId;
        case "chats":
            return view.open.kind === "none" ? null : view.open.podId;
        default:
            return null;
    }
}

/** What the main area has open from the sidebar, if anything. */
export function chatOpenOfView(view: View): ChatOpen {
    return view.name === "chats" ? view.open : NO_CHAT;
}

function decoded(segment: string): string | null {
    try {
        return decodeURIComponent(segment);
    } catch {
        return null;
    }
}

/** `c/{id}`, `g/{id}` or `contacts/{id}`, read against the Lemma it is in.
 *  A part that will not read opens nothing rather than the wrong thing. */
function chatOpenOf(podId: string, rest: readonly (string | undefined)[]): ChatOpen {
    const [kind, id] = rest;
    const named = id ? decoded(id) : null;
    if (!named) return NO_CHAT;
    if (kind === "c") return { kind: "chat", podId, id: named };
    if (kind === "g") return { kind: "shared", podId, id: named };
    if (kind === "contacts" || kind === "customers") return { kind: "contact", podId, id: named };
    return NO_CHAT;
}

function chatTail(open: OpenChat): string {
    switch (open.kind) {
        case "chat": return "/c/" + encodeURIComponent(open.id);
        case "shared": return "/g/" + encodeURIComponent(open.id);
        case "contact": return "/contacts/" + encodeURIComponent(open.id);
    }
}

/** What a `/o` path names. Anything unreadable is Home, the frame's front,
 *  rather than a 404: a link from a newer build should still land somewhere. */
export function readView(pathname: string): View {
    const [root, head, id, tab, ...rest] = pathname.split("/").filter(Boolean);
    if (root !== "o" || !head) return { name: "today" };
    if (head === "outcomes") return { name: "outcomes" };
    if (head === "needs-you") return { name: "needs" };
    if (head === "people") return { name: "people" };
    if (head === "new-lemma") return { name: "hire" };
    if (head === "chats") {
        const podId = id ? decoded(id) : null;
        /* The contacts queue was a chat for a day; it is a Lemma's tab now. */
        if (podId && (tab === "contacts" || tab === "customers") && !rest[0]) return outcomeView(podId, "contacts");
        return { name: "chats", open: podId ? chatOpenOf(podId, [tab, ...rest]) : NO_CHAT };
    }
    if (head === "outcome" && id) {
        const podId = decoded(id);
        if (!podId) return { name: "today" };
        const one = rest[0] ? decoded(rest[0]) : null;
        if (tab === "run" && one) return { name: "run", podId, runId: one };
        if (tab === "app" && one) return { name: "app", podId, app: one };
        if (tab === "table" && one) return { name: "table", podId, table: one };
        if (tab === "workflow" && one) return { name: "workflow", podId, workflow: one };
        if (tab === "agent" && one) return { name: "agent", podId, agent: one };
        if (tab === "record" && one && rest[1]) {
            const recordId = decoded(rest[1]);
            if (recordId) return { name: "record", podId, table: one, recordId };
        }
        if (tab === "settings") return outcomeView(podId, "settings", one);
        if (tab === "file" && rest.length > 0) {
            const parts = rest.map(decoded);
            if (parts.every((part): part is string => part !== null)) return { name: "file", podId, path: "/" + parts.join("/") };
        }
        /* An outcome had a Chats tab for a day; its chats are in the sidebar
           now, and a link to one opens the chat. */
        if (tab === "chats") {
            const open = chatOpenOf(podId, rest);
            return open.kind === "none" ? outcomeView(podId) : chatView(open);
        }
        return outcomeView(podId, OUTCOME_TABS.find((each) => each === tab) ?? "home");
    }
    return { name: "today" };
}

export function writeView(view: View): string {
    switch (view.name) {
        case "today": return "/o";
        case "outcomes": return "/o/outcomes";
        case "needs": return "/o/needs-you";
        case "people": return "/o/people";
        case "hire": return "/o/new-lemma";
        case "chats":
            return view.open.kind === "none" ? "/o/chats" : "/o/chats/" + encodeURIComponent(view.open.podId) + chatTail(view.open);
        case "outcome": {
            const base = "/o/outcome/" + encodeURIComponent(view.podId);
            if (view.tab === "home") return base;
            return base + "/" + view.tab + (view.tab === "settings" && view.section ? "/" + encodeURIComponent(view.section) : "");
        }
        case "run": return "/o/outcome/" + encodeURIComponent(view.podId) + "/run/" + encodeURIComponent(view.runId);
        case "app": return "/o/outcome/" + encodeURIComponent(view.podId) + "/app/" + encodeURIComponent(view.app);
        case "table": return "/o/outcome/" + encodeURIComponent(view.podId) + "/table/" + encodeURIComponent(view.table);
        case "workflow": return "/o/outcome/" + encodeURIComponent(view.podId) + "/workflow/" + encodeURIComponent(view.workflow);
        case "agent": return "/o/outcome/" + encodeURIComponent(view.podId) + "/agent/" + encodeURIComponent(view.agent);
        case "record": return "/o/outcome/" + encodeURIComponent(view.podId) + "/record/" + encodeURIComponent(view.table) + "/" + encodeURIComponent(view.recordId);
        case "file":
            return "/o/outcome/" + encodeURIComponent(view.podId) + "/file/" + view.path.split("/").filter(Boolean).map(encodeURIComponent).join("/");
    }
}
