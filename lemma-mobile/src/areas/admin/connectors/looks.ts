/** What each kind of thing an organization points somewhere itself is
 *  called, and, where it is not obvious, how it signs in (the web's `LOOKS`
 *  in `connect/add-connector.tsx`, which is a component file, so said here
 *  again). Keyed by kind: the ids are catalog data, the kinds are the rule.
 *
 *  The sign-in words are here because the catalog's own field help cannot
 *  say it: an MCP server's OAuth is not a field at all, it is found by
 *  asking the server once the install exists. Pure. */

export interface Look {
    title: string;
    short: string;
    tagline: string;
    blurb: string;
    signIn?: string;
    /** What a connector screen calls its installs of this kind. */
    group: string;
}

export const LOOKS: Readonly<Record<string, Look>> = {
    mcp: {
        title: "An MCP server",
        short: "MCP server",
        tagline: "Tools from a server",
        blurb: "Tools from any server speaking MCP.",
        signIn: "A server that signs in with OAuth is found when you add it, and you sign in from there: leave the token empty for those. Otherwise paste a token, or nothing if it needs none.",
        group: "Servers",
    },
    http: {
        title: "A REST API",
        short: "REST API",
        tagline: "Endpoints from a spec",
        blurb: "Anything with an OpenAPI description. Its operations are read from the spec.",
        signIn: "Signs in with an API key or a token you paste. Signing in with OAuth to an API of your own is not supported yet.",
        group: "APIs",
    },
    sql: {
        title: "A database",
        short: "Database",
        tagline: "PostgreSQL, read-only",
        blurb: "A PostgreSQL database, queried read-only with a username and password you supply.",
        group: "Databases",
    },
};

export function lookOf(kind: string | null | undefined): Look | null {
    return kind ? LOOKS[kind] ?? null : null;
}
