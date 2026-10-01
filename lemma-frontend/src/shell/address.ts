import type { Tab } from "@/data";
import { docTitle } from "@/library/doc-title";
import { readableName } from "@/library/reading";

/** The one segment every workspace URL hangs off. */
export const ROOT = "/t";

/** The organization's own page — every teammate at once, zoomed out — sits
 *  where a pod id would. Pod ids are UUIDs, so no teammate can be called it. */
export const TEAM_SEGMENT = "teammates";

export interface Address {
    /** The teammate. Null on the bare root, where none has been chosen. */
    podId: string | null;
    /** What is in front, as a tab id.
     *
     *  Null means the URL did not say — either a bare `/t/{pod}` or something
     *  this build cannot read. Both are answered the same way: open wherever
     *  this teammate was left and rewrite the address to match, so a link that
     *  arrives from a newer build lands in the workspace instead of on a 404. */
    tabId: string | null;
    /** Which conversation, when the conversation is in front. Null is the
     *  newest one, which is what opening a teammate has always meant. */
    conversationId: string | null;
    /** Which agent is open on the profile.
     *
     *  A path segment rather than a fragment because it is a selection the
     *  profile holds, not a place on the page. The profile's *sections* are a
     *  scrolling page and will want `#skills`, which cannot collide with this
     *  precisely because one is a fragment and the other is a segment. */
    agentName: string | null;
    /** Set on the organization's own page, where no teammate is chosen:
     *  `/t/teammates`. Absent everywhere else, rather than false, so an
     *  address about a teammate reads exactly as it always did. */
    team?: true;
}

export const NOWHERE: Address = { podId: null, tabId: null, conversationId: null, agentName: null };

/** Every teammate at once. */
export const TEAMMATES: Address = { ...NOWHERE, team: true };

/** A URL segment, decoded, or null if it cannot be.
 *
 *  `decodeURIComponent` throws on a lone `%`, which a hand-edited or
 *  half-copied link supplies more often than anyone would like. One bad
 *  segment makes the whole address unreadable rather than a guess. */
function decode(segment: string): string | null {
    try {
        return decodeURIComponent(segment);
    } catch {
        return null;
    }
}

function segmentsOf(pathname: string): string[] | null {
    const raw = pathname.split("/").filter(Boolean);
    const out: string[] = [];
    for (const segment of raw) {
        const decoded = decode(segment);
        if (decoded === null) return null;
        out.push(decoded);
    }
    return out;
}

/** What a URL names.
 *
 *  Never throws and never refuses: an address it cannot read is an address
 *  with no tab in it, which the shell answers by falling back to the
 *  remembered one. A link is somebody's attempt to arrive somewhere, and the
 *  worst outcome is a door that will not open at all.
 */
export function readAddress(pathname: string): Address {
    const segments = segmentsOf(pathname);
    if (!segments || segments[0] !== ROOT.slice(1)) return NOWHERE;

    const podId = segments[1] ?? null;
    if (!podId) return NOWHERE;
    if (podId === TEAM_SEGMENT) return segments.length === 2 ? TEAMMATES : NOWHERE;

    const rest = segments.slice(2);
    const at = (index: number) => rest[index] ?? null;
    const here = (tabId: string | null, extra: Partial<Address> = {}): Address =>
        ({ podId, tabId, conversationId: null, agentName: null, ...extra });

    switch (rest[0]) {
        case undefined:
            return here(null);
        case "conversation":
            /* A third segment or nothing. `/conversation/a/b` names no
               conversation this app can open, so it names none at all. */
            return rest.length > 2 ? here(null) : here("conversation", { conversationId: at(1) });
        /* A space's own lists. `apps` is the Apps list now; the old apps pane
           it used to name is not reachable from anywhere in this app. */
        case "home":
        case "chats":
        case "all":
        case "pages":
        case "apps":
        case "tables":
        case "files":
        case "workflows":
        case "groups":
        case "settings":
        case "about":
            return rest.length > 1 ? here(null) : here("space:" + rest[0]);
        case "library":
        case "history":
        case "computer":
            return rest.length > 1 ? here(null) : here(rest[0]);
        case "profile":
            return rest.length > 2 ? here(null) : here("profile", { agentName: at(1) });
        case "table":
            return rest.length === 2 && rest[1] ? here("table:" + rest[1]) : here(null);
        case "bot":
            return rest.length === 2 && rest[1] ? here("bot:" + rest[1]) : here(null);
        case "run":
            return rest.length === 2 && rest[1] ? here("run:" + rest[1]) : here(null);
        case "workflow":
            return rest.length === 2 && rest[1] ? here("workflow:" + rest[1]) : here(null);
        case "group":
            return rest.length === 2 && rest[1] ? here("group:" + rest[1]) : here(null);
        case "app":
            return rest.length === 2 && rest[1] ? here("app:" + rest[1]) : here(null);
        case "record":
            return rest.length === 3 && rest[1] && rest[2] ? here("record:" + rest[1] + ":" + rest[2]) : here(null);
        case "file":
            /* The rest of the path *is* the path, and the separator between
               `file` and the first segment supplies the leading slash a pod
               file always has: `/t/x/file/me/notes.md` is `/me/notes.md`. */
            return rest.length > 1 ? here("file:/" + rest.slice(1).join("/")) : here(null);
        default:
            return here(null);
    }
}

/** The URL for somewhere. The inverse of `readAddress` for every address that
 *  `readAddress` can produce, which is what the round-trip test asserts. */
export function writeAddress(address: Address): string {
    if (address.team) return ROOT + "/" + TEAM_SEGMENT;
    if (!address.podId) return ROOT;
    const path = [ROOT, encodeURIComponent(address.podId), ...tailOf(address)];
    return path.join("/");
}

function tailOf(address: Address): string[] {
    const { tabId } = address;
    if (!tabId) return [];

    const colon = tabId.indexOf(":");
    const kind = colon < 0 ? tabId : tabId.slice(0, colon);
    const rest = colon < 0 ? "" : tabId.slice(colon + 1);

    switch (kind) {
        case "conversation":
            return address.conversationId
                ? ["conversation", encodeURIComponent(address.conversationId)]
                : ["conversation"];
        case "profile":
            return address.agentName ? ["profile", encodeURIComponent(address.agentName)] : ["profile"];
        case "space":
            return rest ? [rest] : [];
        case "library":
        case "history":
        case "computer":
            return [kind];
        case "table":
        case "app":
        case "bot":
        case "run":
        case "workflow":
        case "group":
            return [kind, encodeURIComponent(rest)];
        case "record": {
            /* The row id may itself carry a colon, so the table name is taken
               off the front rather than the id off the back. */
            const split = rest.indexOf(":");
            if (split < 0) return [];
            return ["record", encodeURIComponent(rest.slice(0, split)), encodeURIComponent(rest.slice(split + 1))];
        }
        case "file":
            /* Each segment encoded on its own: the slashes in a pod path are
               structure and have to stay slashes, and everything else in a
               file name — a `#`, a `?`, a space — must not. */
            return ["file", ...rest.split("/").filter(Boolean).map(encodeURIComponent)];
        default:
            /* A tab kind this function does not know how to spell leaves the
               URL at the teammate rather than writing something unreadable
               back to it. */
            return [];
    }
}

/** The tab a URL names, rebuilt from its id alone.
 *
 *  Most kinds carry everything they need in their own id, so a link to one can
 *  be honoured before a single request has come back. The other three cannot
 *  and do not need to: `conversation`, `library` and `profile` are always in
 *  the pod's own tab list, and an `app:` tab needs the URL and status that
 *  only that list has — so the shell matches those by id once it arrives,
 *  rather than inventing an app frame pointed at nothing.
 */
export function tabFromId(tabId: string): Tab | null {
    if (tabId === "history") return { id: "history", kind: "history", label: "History" };
    if (tabId === "computer") return { id: "computer", kind: "computer", label: "Computer" };

    if (tabId.startsWith("workflow:")) {
        const name = tabId.slice("workflow:".length);
        return name ? { id: tabId, kind: "workflow", label: name, name } : null;
    }
    if (tabId.startsWith("group:")) {
        const groupId = tabId.slice("group:".length);
        /* Named "Group" until the page has read the group and says what it
           is called. */
        return groupId ? { id: tabId, kind: "group", label: "Group", groupId } : null;
    }
    if (tabId.startsWith("run:")) {
        const runId = tabId.slice("run:".length);
        return runId ? { id: tabId, kind: "run", label: "Workflow run", runId } : null;
    }
    if (tabId.startsWith("bot:")) {
        const name = tabId.slice("bot:".length);
        return name ? { id: tabId, kind: "bot", label: readableName(name), name } : null;
    }
    if (tabId.startsWith("table:")) {
        const name = tabId.slice("table:".length);
        return name ? { id: tabId, kind: "table", label: readableName(name), name } : null;
    }
    if (tabId.startsWith("file:")) {
        const path = tabId.slice("file:".length);
        const label = docTitle(path);
        return path ? { id: tabId, kind: "file", label, path } : null;
    }
    if (tabId.startsWith("record:")) {
        const rest = tabId.slice("record:".length);
        const split = rest.indexOf(":");
        if (split < 1 || split === rest.length - 1) return null;
        const table = rest.slice(0, split);
        return { id: tabId, kind: "record", label: readableName(table) + " row", table, recordId: rest.slice(split + 1) };
    }
    return null;
}

/** Whether going from one address to the next is a move worth a Back.
 *
 *  A different space or a different view is. So is a different conversation
 *  or bot profile — but only between two that both exist: a new chat getting
 *  its id is the same place becoming nameable. And an address that named no
 *  view (a bare `/t` or `/t/{pod}`) being filled in is the app answering, not
 *  moving. */
export function isNewPlace(from: Address, to: Address): boolean {
    /* Zooming out to every teammate, or back into one, is a move either way. */
    if (Boolean(from.team) !== Boolean(to.team)) return true;
    if (!from.podId || !from.tabId) return false;
    if (from.podId !== to.podId) return true;
    if (from.tabId !== to.tabId) return true;
    if (from.conversationId && to.conversationId && from.conversationId !== to.conversationId) return true;
    return Boolean(from.agentName && to.agentName && from.agentName !== to.agentName);
}
