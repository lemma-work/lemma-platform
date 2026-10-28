/**
 * Turn a concrete pathname into the route template that produced it.
 *
 * Every URL in this app names the thing it is showing: `/t/{pod}/table/{name}`,
 * `/o/outcome/{pod}/run/{id}`, `/s/{kind}/{…}`. posthog-js attaches the current
 * URL to *every* event it sends -- not just pageviews -- and writes the first one
 * it ever saw into a persistent person property. So without normalisation, the
 * analytics store accumulates a map of which pods, tables, files and
 * conversations exist and who opened them, which is precisely the business
 * context the backend emitter's allowlist exists to keep out.
 *
 * There is no App Router API that hands back the pattern -- `useSelectedLayoutSegments`
 * returns the *values* -- so this is a maintained list plus a drift test that
 * regenerates it from `app/` and fails when the two disagree
 * (`tests/analytics-routes.test.ts`).
 *
 * The workspace (`/t`) and the outcome frame (`/o`) are one catch-all route
 * each, and every place inside them would template to the same row. They are
 * read with their own address grammars instead, so a pageview says which kind
 * of place it was -- a table, a chat, an app -- and never which one.
 */

import { isNewPlace, readAddress } from "@/shell/address";
import { NEW_CHAT, readView, type View } from "@/outcomes/address";

/** Every page route in `app/`, with route groups such as `(marketing)` stripped
 *  because they do not appear in URLs. */
export const ROUTE_TEMPLATES: readonly string[] = [
    "/",
    "/[...legacy]",
    "/about",
    "/auth/[[...path]]",
    "/blog",
    "/blog/[slug]",
    "/changelog",
    "/characters",
    "/characters/arch",
    "/characters/bean",
    "/characters/bloom",
    "/characters/bolt",
    "/characters/cloud",
    "/characters/coil",
    "/characters/cube",
    "/characters/frame",
    "/characters/gem",
    "/characters/grid",
    "/characters/kite",
    "/characters/knot",
    "/characters/loop",
    "/characters/moon",
    "/characters/peak",
    "/characters/pleat",
    "/characters/prism",
    "/characters/ring",
    "/characters/spark",
    "/characters/spool",
    "/characters/sprout",
    "/characters/stack",
    "/characters/wave",
    "/characters/wedge",
    "/connect",
    "/contact",
    "/create-pod",
    "/d/[code]",
    "/demo/computer",
    "/demo/landing",
    "/demo/launch",
    "/demo/loading",
    "/demo/query-context",
    "/docs/[[...slug]]",
    "/download",
    "/import/github/[owner]/[repo]",
    "/invitations/[invitationId]/accept",
    "/invitations/[invitationId]/reject",
    "/logout",
    "/o/[[...path]]",
    "/oauth/complete",
    "/organizations/new",
    "/privacy",
    "/product/[slug]",
    "/product/vignettes",
    "/product/vignettes/[id]",
    "/remix",
    "/s/[kind]/[...path]",
    "/sign-in-to-site/[conversationId]/[toolCallId]",
    "/slack/allowed",
    "/slack/connect",
    "/slack/install",
    "/slack/installed",
    "/t/[[...podId]]",
    "/templates",
    "/templates/[slug]",
    "/tos",
] as const;

/** What an unrecognised path becomes.
 *
 *  Fail closed, like the backend emitter: a path this build does not know is a
 *  path whose segments we cannot vouch for, and echoing it back would defeat the
 *  entire point of this module the first time somebody adds a route. The drift
 *  test keeps this effectively unreachable in production, and the root
 *  `[...legacy]` route catches what is left. */
export const UNMATCHED_ROUTE = "/_unmatched";

type Segment =
    | { kind: "literal"; value: string }
    | { kind: "dynamic" }
    | { kind: "catchAll"; optional: boolean };

function parseTemplate(template: string): Segment[] {
    return splitPath(template).map((raw) => {
        if (raw.startsWith("[[...") && raw.endsWith("]]")) {
            return { kind: "catchAll", optional: true };
        }
        if (raw.startsWith("[...") && raw.endsWith("]")) {
            return { kind: "catchAll", optional: false };
        }
        if (raw.startsWith("[") && raw.endsWith("]")) {
            return { kind: "dynamic" };
        }
        return { kind: "literal", value: raw };
    });
}

function splitPath(path: string): string[] {
    return path.split("/").filter(Boolean);
}

/** How well a template matches, or null for no match.
 *
 *  Score is the number of literal segments matched, so a literal always beats a
 *  dynamic slot at the same position -- and every real route beats the root
 *  `[...legacy]` catch-all, which matches no literal at all. */
function score(segments: Segment[], parts: string[]): number | null {
    let literals = 0;
    let i = 0;

    for (let s = 0; s < segments.length; s += 1) {
        const segment = segments[s];

        if (segment.kind === "catchAll") {
            const remaining = parts.length - i;
            if (remaining === 0) return segment.optional ? literals : null;
            // A catch-all is always last in a Next route, so it takes the rest.
            return literals;
        }

        if (i >= parts.length) return null;

        if (segment.kind === "literal") {
            if (parts[i] !== segment.value) return null;
            literals += 1;
        }
        i += 1;
    }

    return i === parts.length ? literals : null;
}

const PARSED: ReadonlyArray<{ template: string; segments: Segment[] }> =
    ROUTE_TEMPLATES.map((template) => ({
        template,
        segments: parseTemplate(template),
    }));

/** A workspace place, by kind. Ids, names and paths become placeholders; what
 *  survives is either a fixed word of the grammar or a value `readAddress`
 *  only produces from a fixed list (a space's own lists, a part of About). */
function workspaceTemplate(pathname: string): string {
    const address = readAddress(pathname);
    if (address.team) return "/t/teammates";
    if (!address.podId) return "/t";
    const pod = "/t/[pod]";
    const { tabId } = address;
    if (!tabId) return pod;

    const colon = tabId.indexOf(":");
    const kind = colon < 0 ? tabId : tabId.slice(0, colon);
    const rest = colon < 0 ? "" : tabId.slice(colon + 1);
    switch (kind) {
        case "conversation":
            return pod + "/conversation" + (address.conversationId ? "/[id]" : "");
        case "profile":
            return pod + "/profile" + (address.agentName ? "/[agent]" : "");
        case "space":
            return pod + "/" + rest + (rest === "about" && address.section ? "/" + address.section : "");
        case "library":
        case "history":
        case "computer":
            return pod + "/" + kind;
        case "table":
        case "app":
        case "bot":
        case "workflow":
            return pod + "/" + kind + "/[name]";
        case "run":
        case "channel":
            return pod + "/" + kind + "/[id]";
        case "record":
            return pod + "/record/[table]/[id]";
        case "file":
            return pod + "/file/[...path]";
        default:
            return pod;
    }
}

/** An outcome-frame view, by kind, the same way. */
function viewTemplate(view: View): string {
    const pod = "/o/outcome/[pod]";
    switch (view.name) {
        case "today": return "/o";
        case "outcomes": return "/o/outcomes";
        case "needs": return "/o/needs-you";
        case "people": return "/o/people";
        case "hire": return "/o/new-lemma";
        case "chats": {
            const { open } = view;
            if (open.kind === "none") return "/o/chats";
            const tail = open.kind === "chat" ? "c" : open.kind === "shared" ? "g" : "contacts";
            /* A chat not started yet is the one fixed word an id slot holds. */
            const id = open.kind === "chat" && open.id === NEW_CHAT ? NEW_CHAT : "[id]";
            return "/o/chats/[pod]/" + tail + "/" + id;
        }
        case "outcome":
            if (view.tab === "home") return pod;
            return pod + "/" + view.tab + (view.tab === "settings" && view.section ? "/[section]" : "");
        case "run": return pod + "/run/[id]";
        case "app": return pod + "/app/[name]";
        case "table": return pod + "/table/[name]";
        case "workflow": return pod + "/workflow/[name]";
        case "agent": return pod + "/agent/[name]";
        case "record": return pod + "/record/[table]/[id]";
        case "file": return pod + "/file/[...path]";
    }
}

/**
 * Map a concrete pathname onto its route template.
 *
 * Query strings and fragments are not accepted here and are not stripped —
 * callers pass a pathname. Anything unrecognised becomes {@link UNMATCHED_ROUTE}.
 */
export function toRouteTemplate(pathname: string): string {
    const parts = splitPath(pathname);
    if (parts.length === 0) return "/";
    if (parts[0] === "t") return workspaceTemplate(pathname);
    if (parts[0] === "o") return viewTemplate(readView(pathname));

    let best: string | null = null;
    let bestScore = -1;

    for (const { template, segments } of PARSED) {
        const matched = score(segments, parts);
        if (matched === null) continue;
        // Ties go to the first template in the list; the list is sorted, and a
        // genuine tie would mean two routes that Next itself could not
        // distinguish.
        if (matched > bestScore) {
            best = template;
            bestScore = matched;
        }
    }

    return best ?? UNMATCHED_ROUTE;
}

/**
 * Rewrite a full URL so only its origin and route template survive.
 *
 * Returns null when the input is not a parseable URL, which callers treat as
 * "drop the property" rather than "pass it through".
 */
export function toTemplateUrl(rawUrl: string): string | null {
    try {
        const url = new URL(rawUrl);
        return `${url.origin}${toRouteTemplate(url.pathname)}`;
    } catch {
        return null;
    }
}

/**
 * Whether moving from one pathname to the next is a new pageview.
 *
 * Both frames rewrite their own address as they settle: a bare `/t` becomes the
 * place it opened, and a new chat gets its id once it has one. Each rewrite
 * changes the pathname, and counting each one made every arrival two or three
 * pageviews. The workspace already decides which moves are worth a Back
 * (`isNewPlace`), and a pageview is the same question. The outcome frame's
 * only such rewrite is a new chat being named.
 */
export function isNewView(previous: string | null, next: string): boolean {
    if (previous === null) return true;
    if (previous === next) return false;
    const [from] = splitPath(previous);
    const [to] = splitPath(next);
    if (from === "t" && to === "t") return isNewPlace(readAddress(previous), readAddress(next));
    if (from === "o" && to === "o") {
        const before = readView(previous);
        const after = readView(next);
        const named =
            before.name === "chats" && after.name === "chats" &&
            before.open.kind === "chat" && after.open.kind === "chat" &&
            before.open.podId === after.open.podId && before.open.id === NEW_CHAT;
        return !named;
    }
    return true;
}
