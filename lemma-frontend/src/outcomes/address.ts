/** The app's address grammar: which view a path names, and the path for a
 *  view. Kept apart from `model.ts` and free of imports, so code that only
 *  needs to read an address — analytics templating one, on every page, or
 *  the server sending an old link on — does not carry the plan parser and
 *  the channel model with it. */

/** A Lemma's pages. Its row of tabs is the first seven: its home, its chats,
 *  its apps, its tables, its pages, its files, and About. The rest are parts
 *  of About -- what it runs, who is in it, who writes in from outside, its
 *  plan, its settings -- each still a page of its own with its own address. */
export type OutcomeTab = "home" | "chats" | "apps" | "tables" | "pages" | "files" | "about" | "automations" | "people" | "contacts" | "settings";

/** What a Lemma runs, in one place: its workflows and their runs, its
 *  schedules, its agents, and the X-ray of all of it. The workspace keeps
 *  these on four pages; here they are one tab, a view each. */
export const AUTOMATION_SECTIONS = ["workflows", "waiting", "running", "history", "schedules", "agents", "xray"] as const;
export type AutomationSection = (typeof AUTOMATION_SECTIONS)[number];

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
    /** Every app across the Lemmas. */
    | { name: "apps" }
    /** The services the organization's Lemmas can work through. */
    | { name: "connectors" }
    | { name: "chats"; open: ChatOpen }
    | { name: "people" }
    /** What the reader's Lemmas have been doing: every one of them, or the
     *  one named. */
    | { name: "history"; podId: string | null }
    /** `section` is a part of a Lemma's settings (its About), or one view
     *  of its automations, on that tab. */
    | { name: "outcome"; podId: string; tab: OutcomeTab; section?: string }
    | { name: "run"; podId: string; runId: string }
    | { name: "app"; podId: string; app: string }
    | { name: "file"; podId: string; path: string }
    | { name: "table"; podId: string; table: string }
    | { name: "workflow"; podId: string; workflow: string }
    | { name: "agent"; podId: string; agent: string }
    | { name: "record"; podId: string; table: string; recordId: string }
    /** The machine a Lemma works on: its screen and its files, from one
     *  chat's working folder, or the whole machine. */
    | { name: "computer"; podId: string; conversationId: string | null }
    /** Starting a Lemma from scratch, the workspace's own way. */
    | { name: "hire" };

export const OUTCOME_TABS: readonly OutcomeTab[] = ["home", "chats", "apps", "tables", "pages", "files", "about", "automations", "people", "contacts", "settings"];

/** The tabs a Lemma's row shows, in order. */
export const ROW_TABS: readonly OutcomeTab[] = ["home", "chats", "apps", "tables", "pages", "files", "about"];

/** Whether a page is a part of About rather than a tab of its own: the row
 *  keeps About lit over it, and the page offers the way back. */
export function partOfAbout(tab: OutcomeTab): boolean {
    return !ROW_TABS.includes(tab);
}

/** A Lemma's page, on its home unless another tab is named. */
export function outcomeView(podId: string, tab: OutcomeTab = "home", section: string | null = null): View {
    return section && (tab === "settings" || tab === "automations" || tab === "chats") ? { name: "outcome", podId, tab, section } : { name: "outcome", podId, tab };
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
        case "computer":
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

/** `c/{id}`, `g/{id}` or `contacts/{id}` in the frame's first grammar,
 *  read against the Lemma it is in. A part that will not read opens nothing
 *  rather than the wrong thing. */
function trialChatOf(podId: string, rest: readonly (string | undefined)[]): ChatOpen {
    const [kind, id] = rest;
    const named = id ? decoded(id) : null;
    if (!named) return NO_CHAT;
    if (kind === "c") return { kind: "chat", podId, id: named };
    if (kind === "g") return { kind: "shared", podId, id: named };
    if (kind === "contacts" || kind === "customers") return { kind: "contact", podId, id: named };
    return NO_CHAT;
}

/* ── addresses ──────────────────────────────────────────────────────────
 *
 * Plain words, one noun. The places are words of their own -- `/home`,
 * `/needs`, `/apps`, `/connectors`, `/people`, `/chats`, `/history` -- and everything
 * about one Lemma sits under `/l/{id}`, in plural words for its sections
 * and the same word with a name for one thing in it: `/l/{id}/apps` lists
 * them, `/l/{id}/apps/{name}` opens one. `/l` alone is every Lemma.
 *
 * The workspace's own addresses lived under `/l` before this; where a word
 * is in both, it means the same place, and a word only the workspace used
 * (`conversation`, `file`, a part under `about`…) is not one of these, so an
 * old link is told apart and read the old way (`viewOfAddress`). */

/** The app's places that are not inside one Lemma. */
const PLACES: Readonly<Record<string, View>> = {
    home: { name: "today" },
    needs: { name: "needs" },
    apps: { name: "apps" },
    connectors: { name: "connectors" },
    people: { name: "people" },
    chats: { name: "chats", open: NO_CHAT },
};

export const LEMMAS = "/l";
export const HOME = "/home";

/** The rest of a path, decoded, or null if any part will not read. */
function decodedAll(parts: readonly string[]): string[] | null {
    const out = parts.map(decoded);
    return out.every((part): part is string => part !== null) ? out : null;
}

/** What an address names, or null when it is not one of the app's own --
 *  an old link, a workspace address -- for the caller to read another way. */
export function readView(pathname: string): View | null {
    const parts = pathname.split("/").filter(Boolean);
    const [head, ...after] = parts;
    if (!head) return null;
    /* Its own places only: `/constructor` is not one because every object
       answers to it. */
    if (head === "history") {
        if (after.length === 0) return { name: "history", podId: null };
        const of = after.length === 1 ? decoded(after[0]) : null;
        return of ? { name: "history", podId: of } : null;
    }
    if (head !== "l") return after.length === 0 && Object.hasOwn(PLACES, head) ? PLACES[head] : null;
    const [id, section, ...tail] = after;
    if (!id) return { name: "outcomes" };
    if (id === "new") return after.length === 1 ? { name: "hire" } : null;
    /* The workspace's list of every teammate, from its own addresses. */
    if (id === "teammates") return after.length === 1 ? { name: "outcomes" } : null;
    const podId = decoded(id);
    if (!podId) return null;
    const rest = decodedAll(tail);
    if (!rest) return null;
    const [one, two] = rest;
    switch (section) {
        case undefined: return outcomeView(podId);
        /* The plan a Lemma kept for a result it owned, before what it is
           taught lived in its memory: an old link opens About, which says
           what it has been taught. */
        case "plan": return rest.length === 0 ? outcomeView(podId, "about") : null;
        case "chats":
            if (rest.length === 0) return outcomeView(podId, "chats");
            return rest.length === 1 ? chatView({ kind: "chat", podId, id: one }) : null;
        case "groups":
            if (rest.length === 0) return outcomeView(podId, "chats", "channels");
            return rest.length === 1 ? chatView({ kind: "shared", podId, id: one }) : null;
        case "contacts":
            if (rest.length === 0) return outcomeView(podId, "contacts");
            return rest.length === 1 ? chatView({ kind: "contact", podId, id: one }) : null;
        case "apps":
            if (rest.length === 0) return outcomeView(podId, "apps");
            return rest.length === 1 ? { name: "app", podId, app: one } : null;
        case "pages": return rest.length === 0 ? outcomeView(podId, "pages") : null;
        /* About alone is this app's page. With a part named after it, it is
           the workspace's old address, which its own grammar reads. */
        case "about": return rest.length === 0 ? outcomeView(podId, "about") : null;
        case "people": return rest.length === 0 ? outcomeView(podId, "people") : null;
        case "files":
            return rest.length === 0 ? outcomeView(podId, "files") : { name: "file", podId, path: "/" + rest.join("/") };
        case "tables":
            if (rest.length === 0) return outcomeView(podId, "tables");
            if (rest.length === 1) return { name: "table", podId, table: one };
            return rest.length === 2 ? { name: "record", podId, table: one, recordId: two } : null;
        case "automations": {
            if (rest.length === 0) return outcomeView(podId, "automations");
            const view = AUTOMATION_SECTIONS.find((each) => each === one);
            return view && rest.length === 1 ? outcomeView(podId, "automations", view) : null;
        }
        case "workflows":
            if (rest.length === 0) return outcomeView(podId, "automations", "workflows");
            return rest.length === 1 ? { name: "workflow", podId, workflow: one } : null;
        case "runs":
            if (rest.length === 0) return outcomeView(podId, "automations", "history");
            return rest.length === 1 ? { name: "run", podId, runId: one } : null;
        case "agents":
            if (rest.length === 0) return outcomeView(podId, "automations", "agents");
            return rest.length === 1 ? { name: "agent", podId, agent: one } : null;
        case "settings":
            return rest.length <= 1 ? outcomeView(podId, "settings", one ?? null) : null;
        case "computer":
            return rest.length <= 1 ? { name: "computer", podId, conversationId: one ?? null } : null;
        default:
            return null;
    }
}

const e = encodeURIComponent;

/** A Lemma's section, as its address names it. */
const SECTION_WORDS: Readonly<Record<OutcomeTab, string>> = {
    home: "",
    chats: "chats",
    pages: "pages",
    tables: "tables",
    files: "files",
    apps: "apps",
    about: "about",
    automations: "automations",
    people: "people",
    contacts: "contacts",
    settings: "settings",
};

/** The address for somewhere: the inverse of `readView`, which the
 *  round-trip test holds it to. */
export function writeView(view: View): string {
    switch (view.name) {
        case "today": return HOME;
        case "needs": return "/needs";
        case "apps": return "/apps";
        case "connectors": return "/connectors";
        case "people": return "/people";
        case "history": return "/history" + (view.podId ? "/" + e(view.podId) : "");
        case "outcomes": return LEMMAS;
        case "hire": return LEMMAS + "/new";
        case "chats": {
            const open = view.open;
            if (open.kind === "none") return "/chats";
            const word = open.kind === "chat" ? "chats" : open.kind === "shared" ? "groups" : "contacts";
            return LEMMAS + "/" + e(open.podId) + "/" + word + "/" + e(open.id);
        }
        case "outcome": {
            const base = LEMMAS + "/" + e(view.podId);
            if (view.tab === "home") return base;
            if (view.tab === "chats" && view.section === "channels") return base + "/groups";
            const tail = (view.tab === "settings" || view.tab === "automations") && view.section ? "/" + e(view.section) : "";
            return base + "/" + SECTION_WORDS[view.tab] + tail;
        }
        case "run": return LEMMAS + "/" + e(view.podId) + "/runs/" + e(view.runId);
        case "app": return LEMMAS + "/" + e(view.podId) + "/apps/" + e(view.app);
        case "table": return LEMMAS + "/" + e(view.podId) + "/tables/" + e(view.table);
        case "record": return LEMMAS + "/" + e(view.podId) + "/tables/" + e(view.table) + "/" + e(view.recordId);
        case "workflow": return LEMMAS + "/" + e(view.podId) + "/workflows/" + e(view.workflow);
        case "agent": return LEMMAS + "/" + e(view.podId) + "/agents/" + e(view.agent);
        case "computer": return LEMMAS + "/" + e(view.podId) + "/computer" + (view.conversationId ? "/" + e(view.conversationId) : "");
        case "file": return LEMMAS + "/" + e(view.podId) + "/files/" + view.path.split("/").filter(Boolean).map(e).join("/");
    }
}

/* ── the frame's first addresses ─────────────────────────────────────── */

/** The first words after `/t` (and `/o`, where the frame was tried before
 *  it was `/t`) in the frame's first grammar. Anything else there was a
 *  workspace address. */
const TRIAL_HEADS: ReadonlySet<string> = new Set(["outcomes", "needs-you", "apps", "connectors", "people", "new-lemma", "chats", "outcome"]);

/** What an address in the frame's first grammar -- `/t/outcome/{id}/…`,
 *  `/t/chats/{id}/c/{chat}`, `/t/needs-you` -- named, so an old link opens
 *  the same place at its new address. Null for anything else. */
export function readTrialView(pathname: string): View | null {
    const [root, head, id, tab, ...rest] = pathname.split("/").filter(Boolean);
    if (root !== "t" && root !== "o") return null;
    if (!head) return { name: "today" };
    if (!TRIAL_HEADS.has(head)) return null;
    if (head === "outcomes") return { name: "outcomes" };
    if (head === "needs-you") return { name: "needs" };
    if (head === "apps") return { name: "apps" };
    if (head === "connectors") return { name: "connectors" };
    if (head === "people") return { name: "people" };
    if (head === "new-lemma") return { name: "hire" };
    if (head === "chats") {
        const podId = id ? decoded(id) : null;
        /* The contacts queue was a chat for a day; it is a Lemma's tab now. */
        if (podId && (tab === "contacts" || tab === "customers") && !rest[0]) return outcomeView(podId, "contacts");
        return { name: "chats", open: podId ? trialChatOf(podId, [tab, ...rest]) : NO_CHAT };
    }
    if (!id) return { name: "today" };
    const podId = decoded(id);
    if (!podId) return { name: "today" };
    const one = rest[0] ? decoded(rest[0]) : null;
    if (tab === "run" && one) return { name: "run", podId, runId: one };
    if (tab === "app" && one) return { name: "app", podId, app: one };
    if (tab === "table" && one) return { name: "table", podId, table: one };
    if (tab === "workflow" && one) return { name: "workflow", podId, workflow: one };
    if (tab === "agent" && one) return { name: "agent", podId, agent: one };
    if (tab === "computer") return { name: "computer", podId, conversationId: one };
    if (tab === "record" && one && rest[1]) {
        const recordId = decoded(rest[1]);
        if (recordId) return { name: "record", podId, table: one, recordId };
    }
    if (tab === "settings") return outcomeView(podId, "settings", one);
    if (tab === "automations") return outcomeView(podId, "automations", AUTOMATION_SECTIONS.find((each) => each === one) ?? null);
    /* X-ray was a tab of its own; it is a view of Automations now. */
    if (tab === "xray") return outcomeView(podId, "automations", "xray");
    if (tab === "file" && rest.length > 0) {
        const parts = decodedAll(rest);
        if (parts) return { name: "file", podId, path: "/" + parts.join("/") };
    }
    /* A Lemma's every chat, searchable; `channels` opens it on the shared
       ones. A link with a chat named under it, from when the list was a
       tab, opens that chat. */
    if (tab === "chats") {
        if (one === "channels" && rest.length === 1) return outcomeView(podId, "chats", "channels");
        const open = trialChatOf(podId, rest);
        return open.kind === "none" ? outcomeView(podId, "chats") : chatView(open);
    }
    return outcomeView(podId, OUTCOME_TABS.find((each) => each === tab) ?? "home");
}
