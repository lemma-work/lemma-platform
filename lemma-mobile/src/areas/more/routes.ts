import { part } from "../paths";

function query(pairs: Record<string, string | null | undefined>): string {
    const said = Object.entries(pairs).filter((pair): pair is [string, string] => Boolean(pair[1]));
    return said.length ? "?" + said.map(([name, value]) => name + "=" + part(value)).join("&") : "";
}

/** Search, starting out, sharing, the agent's computer, calls and the tour.
 *
 *  Where a place has a page on the web, the phone's screen sits at the same
 *  path (`invitations/{id}/accept`, `import/github/{owner}/{repo}`, `remix`,
 *  `templates`, `sign-in-to-site/{conversation}/{call}`), so a `lemma://`
 *  link written in the web's grammar opens the phone's own screen, and
 *  `+native-intent.tsx` only has to drop the host from an `https://` one. */
export const moreRoutes = {
    /** Everything in every Lemma; `podId` starts it on one. */
    search: "/search" as const,
    searchIn: (podId: string) => "/search" + query({ pod: podId }),
    /** The first morning: invitations, organizations to join, a new one.
     *  Home is this when there is no organization yet. */
    welcome: "/welcome" as const,
    newOrg: "/new-org" as const,
    /** Starting a Lemma: what it owns, or a template. */
    newLemma: "/new-lemma" as const,
    /** A Lemma that is not one of yours, and asking to join it. */
    join: (podId: string) => `/join/${part(podId)}`,
    invitation: (id: string, decision: "accept" | "reject" = "accept") => `/invitations/${part(id)}/${decision}`,
    templates: "/templates" as const,
    install: (owner: string, repo: string) => `/import/github/${part(owner)}/${part(repo)}`,
    remix: (source: string) => "/remix" + query({ source }),
    /** The computer, opened on a chat's folder when one is named. */
    computer: (at: { podId?: string | null; conversationId?: string | null } = {}) => "/computer" + query({ pod: at.podId, conversation: at.conversationId }),
    /** A site waiting on a person to sign in, by the paused call. */
    signIn: (conversationId: string, toolCallId: string) => `/sign-in-to-site/${part(conversationId)}/${part(toolCallId)}`,
    /** A voice call with a Lemma, about the chat that was open. */
    call: (podId: string, conversationId?: string | null) => `/call/${part(podId)}` + query({ conversation: conversationId }),
    /** How a place works: pages, workflows, tables, apps. */
    guide: (place: "pages" | "workflows" | "tables" | "apps", podId?: string | null) => `/guide/${part(place)}` + query({ pod: podId }),
};
