/**
 * Turn a concrete pathname into the route template that produced it.
 *
 * Every URL in this app names the thing it is showing: `/pod/{uuid}/agents/{uuid}`,
 * `/pod/{uuid}/flows/{uuid}/runs/{uuid}`, `/s/{kind}/pod/{uuid}`. posthog-js
 * attaches the current URL to *every* event it sends -- not just pageviews --
 * and writes the first one it ever saw into a persistent person property. So
 * without normalisation, the analytics store accumulates a map of which pods,
 * agents, flows and conversations exist and who opened them, which is precisely
 * the business context the backend emitter's allowlist exists to keep out.
 *
 * There is no App Router API that hands back the pattern -- `useSelectedLayoutSegments`
 * returns the *values* -- so this is a maintained list plus a drift test that
 * regenerates it from `app/` and fails when the two disagree.
 */

/** Every page route in `app/`, with route groups `(auth)`/`(dashboard)` stripped
 *  because they do not appear in URLs. Kept in sync by `route-template.test.ts`. */
export const ROUTE_TEMPLATES: readonly string[] = [
    "/",
    "/about",
    "/auth/[[...path]]",
    "/blog",
    "/blog/[slug]",
    "/changelog",
    "/connectors",
    "/contact",
    "/conversations",
    "/create-pod",
    "/docs",
    "/docs/[...slug]",
    "/docs/how-lemma-works",
    "/download",
    "/home",
    "/import/github/[owner]/[repo]",
    "/invitations/[invitationId]/accept",
    "/invitations/[invitationId]/reject",
    "/landing",
    "/loading-preview",
    "/login",
    "/logout",
    "/organizations/[id]/settings/agent-runtimes",
    "/organizations/[id]/settings/members",
    "/organizations/[id]/settings/usage",
    "/organizations/new",
    "/pod/[id]",
    "/pod/[id]/agents/[agentId]",
    "/pod/[id]/agents/new",
    "/pod/[id]/ai",
    "/pod/[id]/ai/assistant",
    "/pod/[id]/app/pages",
    "/pod/[id]/app/view",
    "/pod/[id]/assistants",
    "/pod/[id]/assistants/[assistantId]",
    "/pod/[id]/assistants/new",
    "/pod/[id]/channels",
    "/pod/[id]/connectors",
    "/pod/[id]/conversations",
    "/pod/[id]/conversations/[conversationId]",
    "/pod/[id]/data",
    "/pod/[id]/files",
    "/pod/[id]/flows",
    "/pod/[id]/flows/[flowId]",
    "/pod/[id]/flows/[flowId]/runs/[runId]",
    "/pod/[id]/flows/new",
    "/pod/[id]/functions",
    "/pod/[id]/functions/[functionId]",
    "/pod/[id]/functions/new",
    "/pod/[id]/notifications",
    "/pod/[id]/schedules",
    "/pod/[id]/settings",
    "/pod/[id]/settings/automation",
    "/pod/[id]/settings/members",
    "/pod/[id]/settings/models",
    "/pod/[id]/settings/usage",
    "/pod/[id]/surfaces",
    "/pod/[id]/widgets/view",
    "/pods",
    "/privacy",
    "/profile",
    "/profile/usage",
    "/remix",
    "/s/[kind]/[...path]",
    "/signup",
    "/templates",
    "/templates/[slug]",
    "/terms",
    "/tos",
] as const;

/** What an unrecognised path becomes.
 *
 *  Fail closed, like the backend emitter: a path this build does not know is a
 *  path whose segments we cannot vouch for, and echoing it back would defeat the
 *  entire point of this module the first time somebody adds a route. The drift
 *  test keeps this effectively unreachable in production. */
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
 *  dynamic slot at the same position. That is what keeps `/pod/x/agents/new`
 *  from being read as an agent whose id is the word "new" -- a collision this
 *  app has six of. */
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
    ROUTE_TEMPLATES.map((template) => ({ template, segments: parseTemplate(template) }));

/**
 * Map a concrete pathname onto its route template.
 *
 * Query strings and fragments are not accepted here and are not stripped —
 * callers pass a pathname. Anything unrecognised becomes {@link UNMATCHED_ROUTE}.
 */
export function toRouteTemplate(pathname: string): string {
    const parts = splitPath(pathname);
    if (parts.length === 0) return "/";

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
