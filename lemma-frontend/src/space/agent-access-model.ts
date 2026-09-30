import type { Pod } from "@/data";

/** The words and commands behind Settings › Coding agents, kept apart from
 *  the component so the quoting — the part a person pastes into a shell — is
 *  tested rather than trusted. */

export type Tool = { id: string; label: string; target: string; launch: ((prompt: string) => string) | null };

export const TOOLS: Tool[] = [
    { id: "claude", label: "Claude Code", target: "claude", launch: prompt => "claude " + quote(prompt) },
    { id: "codex", label: "Codex", target: "codex", launch: prompt => "codex " + quote(prompt) },
    { id: "cursor", label: "Cursor", target: "cursor --scope project", launch: null },
    { id: "opencode", label: "OpenCode", target: "opencode", launch: null },
];

/** Single-quoted for a POSIX shell, where nothing inside is special. */
export function quote(text: string): string {
    return "'" + text.replace(/'/g, "'\\''") + "'";
}

/** The CLI ships knowing the cloud; anything else has to be introduced. */
export function serverSteps(apiUrl: string | null, siteOrigin: string): string[] {
    if (!apiUrl) return [];
    let host: URL;
    try {
        host = new URL(apiUrl);
    } catch {
        return [];
    }
    if (host.hostname === "api.lemma.work") return [];
    const local = host.hostname === "localhost" || host.hostname === "127.0.0.1";
    /* Named for the deployment, not its API subdomain: api.acme.dev is "acme". */
    const name = local ? "local" : host.hostname.replace(/^api\./, "").split(".")[0] || "lemma";
    return [
        "lemma servers create " + name + " --base-url " + host.origin + " --auth-url " + siteOrigin + "/auth",
        "lemma servers select " + name,
    ];
}

export function setupCommands(pod: Pod, tool: Tool, servers: string[]): string[] {
    return [
        "uv tool install lemma-terminal",
        ...servers,
        "lemma auth login",
        "lemma skills install --target " + tool.target,
        "lemma pods select " + pod.id + " --save-default",
        "lemma describe",
    ];
}

export function setupPrompt(pod: Pod, tool: Tool, servers: string[]): string {
    const steps = setupCommands(pod, tool, servers);
    return "Set me up to use my Lemma space “" + pod.name + "” from here. "
        + "Skip the first step if the lemma command already exists. Run these in order, and when "
        + "lemma auth login opens a browser, wait for me to finish signing in:\n\n"
        + steps.map((step, index) => (index + 1) + ". " + step).join("\n")
        + "\n\nThen tell me in a few lines what is in the space. If the new skills need a restart to load, say so.";
}

/** Things worth asking once it is set up, each naming the space so the
 *  prompt still works in a folder bound to a different one. */
export function starterPrompts(pod: Pod): { title: string; prompt: string }[] {
    const space = "the Lemma space “" + pod.name + "” (pod " + pod.id + ")";
    return [
        {
            title: "Catch me up",
            prompt: "Using the lemma CLI, look at " + space + " and tell me what changed this week: pages written or edited, "
                + "new table rows, and workflow runs that failed or are waiting on someone. Keep it short and name each thing.",
        },
        {
            title: "Write a page from this repo",
            prompt: "Read the recent commits in this repo and write a page in " + space + " under /pages that explains what shipped, "
                + "for people who do not read code. Upload it with the lemma CLI and give me its path.",
        },
        {
            title: "Load data into a table",
            prompt: "Load the data I point you at into a table in " + space + ", creating the table if it does not exist. "
                + "Show me the columns you plan before writing anything.",
        },
        {
            title: "Add a workflow",
            prompt: "Add a workflow to " + space + ". Ask me what should start it and what it should do, "
                + "then build it with the lemma CLI and turn it on.",
        },
        {
            title: "Build an app on a table",
            prompt: "Build a small app in " + space + " on top of one of its tables. List the tables with the lemma CLI first, "
                + "then ask me which one and what the app is for.",
        },
    ];
}

/* ── Connecting a space by URL, over MCP ─────────────────────────────── */

/** The URL a space is added to an MCP client by. The API serves one per
 *  space, and a token signed in for it works on that space and no other. */
export function mcpUrl(apiUrl: string | null, podId: string): string | null {
    if (!apiUrl) return null;
    try {
        const base = new URL(apiUrl);
        return base.origin + base.pathname.replace(/\/+$/, "") + "/mcp/" + podId;
    } catch {
        return null;
    }
}

/** Claude and ChatGPT connect from their own servers, not from this browser,
 *  so an API on this machine is out of their reach. Saying so beats a
 *  connector that fails to add with an error about the network. */
export function reachableFromInternet(apiUrl: string | null): boolean {
    if (!apiUrl) return false;
    try {
        const host = new URL(apiUrl).hostname;
        return !(host === "localhost" || host === "127.0.0.1" || host === "[::1]" || host.endsWith(".localhost"));
    } catch {
        return false;
    }
}

/** The name the space goes by in a client's list of servers. */
export function serverName(pod: Pick<Pod, "name">): string {
    const slug = pod.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
    return slug ? "lemma-" + slug : "lemma";
}

export type McpClient = {
    id: string;
    label: string;
    /** Connects from its own servers, so it needs an API on the internet. */
    remote: boolean;
    command: ((url: string, pod: Pick<Pod, "name">) => string) | null;
    steps: (url: string, pod: Pick<Pod, "name">) => string[];
};

export const MCP_CLIENTS: McpClient[] = [
    {
        id: "claude",
        label: "Claude",
        remote: true,
        command: null,
        steps: url => [
            "In Claude, open Customize › Connectors and choose Add custom connector.",
            "Paste " + url + " and add it.",
            "Choose Connect, sign in to Lemma, and allow access.",
        ],
    },
    {
        id: "claude-code",
        label: "Claude Code",
        remote: false,
        command: (url, pod) => "claude mcp add --transport http " + serverName(pod) + " " + url,
        steps: (_url, pod) => [
            "Run the command in the folder you work in.",
            "In Claude Code, run /mcp, choose " + serverName(pod) + ", sign in to Lemma and allow access.",
        ],
    },
    {
        id: "chatgpt",
        label: "ChatGPT",
        remote: true,
        command: null,
        steps: url => [
            "In ChatGPT on the web, turn on developer mode under Settings › Security and login.",
            "Add a new connector with " + url + " and choose OAuth.",
            "Sign in to Lemma when asked, and allow access.",
        ],
    },
    {
        id: "other",
        label: "Other",
        remote: false,
        command: null,
        steps: url => [
            "Add " + url + " as a remote MCP server (streamable HTTP).",
            "A client that signs in with OAuth sends you here to allow access; nothing else to configure.",
        ],
    },
];

/* ── Connected clients ───────────────────────────────────────────────── */

export type ConnectedClient = {
    grant_id: string;
    client_name: string;
    scopes: string[];
    connected_at: string;
    last_used_at: string | null;
};

/** "Read and write", "Read only": what a person agreed to, in their words. */
export function accessLabel(scopes: string[]): string {
    return scopes.includes("pod:write") ? "Read and write" : "Read only";
}

export async function listConnectedClients(
    apiUrl: string,
    podId: string,
    fetcher: typeof fetch = fetch,
): Promise<ConnectedClient[]> {
    const response = await fetcher(apiUrl + "/oauth/grants?pod_id=" + encodeURIComponent(podId), {
        credentials: "include",
        cache: "no-store",
        headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error("Connected apps could not be loaded (" + response.status + ").");
    const body = (await response.json()) as { items?: ConnectedClient[] };
    return Array.isArray(body.items) ? body.items : [];
}

/** Ends the connection and every token it was given; the app has to ask
 *  again to come back. A 404 means it is already gone, which is the outcome
 *  asked for. */
/** The URL as the API states it. The browser knows the API by the address
 *  this page was configured with, which is not always the one outside
 *  clients reach; the API knows its public one. */
export async function fetchMcpUrl(apiUrl: string, podId: string, fetcher: typeof fetch = fetch): Promise<string | null> {
    const response = await fetcher(apiUrl + "/oauth/mcp-endpoint/" + encodeURIComponent(podId), {
        credentials: "include",
        headers: { Accept: "application/json" },
    });
    if (!response.ok) return null;
    const body = (await response.json()) as { url?: unknown };
    return typeof body.url === "string" ? body.url : null;
}

export async function disconnectClient(apiUrl: string, grantId: string, fetcher: typeof fetch = fetch): Promise<void> {
    const response = await fetcher(apiUrl + "/oauth/grants/" + encodeURIComponent(grantId), {
        method: "DELETE",
        credentials: "include",
    });
    if (!response.ok && response.status !== 404) throw new Error("It could not be disconnected (" + response.status + ").");
}
