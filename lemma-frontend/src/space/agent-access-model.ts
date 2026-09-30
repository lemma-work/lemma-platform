import type { Pod } from "@/data";

/** The words and commands behind Settings › AI tools, kept apart from
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

/** Claude and ChatGPT connect from their own servers, not from this browser,
 *  so an API on this machine, or on a private network, is out of their reach.
 *  Saying so beats a connector that fails to add with an error about the
 *  network. */
export function reachableFromInternet(apiUrl: string | null): boolean {
    if (!apiUrl) return false;
    let host: string;
    try {
        host = new URL(apiUrl).hostname.toLowerCase();
    } catch {
        return false;
    }
    if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal")) return false;
    const v4 = host.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
    if (v4) {
        const [a, b] = [Number(v4[1]), Number(v4[2])];
        return !(a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168)
            || (a === 169 && b === 254) || (a === 100 && b >= 64 && b <= 127) || a === 0);
    }
    if (host.startsWith("[")) {
        const v6 = host.slice(1, -1);
        return !(v6 === "::1" || v6.startsWith("fc") || v6.startsWith("fd") || v6.startsWith("fe80"));
    }
    return true;
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

/** What to call the space in a client's list of connectors. */
export function connectorName(pod: Pick<Pod, "name">): string {
    return "Lemma – " + pod.name;
}

/** The clicks, in each client's own words, as they are today. Kept to what a
 *  person does: the link is copied above, so no step repeats it. */
export const MCP_CLIENTS: McpClient[] = [
    {
        id: "claude",
        label: "Claude",
        remote: true,
        command: null,
        steps: (_url, pod) => [
            "In Claude, open Customize › Connectors, then Add › Add custom connector.",
            "Name it “" + connectorName(pod) + "”, paste the link, and choose Add.",
            "Choose Connect and allow Lemma. Then just ask Claude about " + pod.name + ".",
        ],
    },
    {
        id: "chatgpt",
        label: "ChatGPT",
        remote: true,
        command: null,
        steps: (_url, pod) => [
            "In ChatGPT, open Plugins, then Add › Create MCP App.",
            "Name it “" + connectorName(pod) + "”, paste the link under Connection, tick “I understand”, and choose Create.",
            "Choose Continue and allow Lemma. Then type @" + connectorName(pod) + " in any chat.",
        ],
    },
    {
        id: "claude-code",
        label: "Claude Code",
        remote: false,
        command: (url, pod) => "claude mcp add --transport http " + serverName(pod) + " " + url,
        steps: (_url, pod) => [
            "Run the command in the folder you work in.",
            "In Claude Code, run /mcp, choose " + serverName(pod) + " and allow Lemma.",
        ],
    },
    {
        id: "other",
        label: "Other",
        remote: false,
        command: null,
        steps: () => [
            "Add the link as a remote MCP server.",
            "When it asks you to sign in, allow Lemma. Nothing else to set up.",
        ],
    },
];

/* ── Connected clients ───────────────────────────────────────────────── */

export type ConnectedClient = {
    grant_id: string;
    /** The person who connected it. */
    user_id: string;
    client_name: string;
    scopes: string[];
    connected_at: string;
    last_used_at: string | null;
};

/** "Read and write", "Read only": what a person agreed to, in their words. */
export function accessLabel(scopes: string[]): string {
    return scopes.includes("pod:write") ? "Read and write" : "Read only";
}

/** The connections to show: everyone's, for the space's admins, who answer
 *  for what can read it; otherwise the person's own. The API decides who is
 *  an admin; asking for everyone and being refused is how this finds out. */
export async function loadConnections(
    apiUrl: string,
    podId: string,
    fetcher: typeof fetch = fetch,
): Promise<{ items: ConnectedClient[]; everyone: boolean }> {
    const url = (everyone: boolean) =>
        apiUrl + "/oauth/grants?pod_id=" + encodeURIComponent(podId) + (everyone ? "&everyone=true" : "");
    const init: RequestInit = { credentials: "include", cache: "no-store", headers: { Accept: "application/json" } };
    let response = await fetcher(url(true), init);
    let everyone = true;
    if (response.status === 403) {
        response = await fetcher(url(false), init);
        everyone = false;
    }
    if (!response.ok) throw new Error("Connected apps could not be loaded (" + response.status + ").");
    const body = (await response.json()) as { items?: ConnectedClient[] };
    return { items: Array.isArray(body.items) ? body.items : [], everyone };
}

/** The URL as the API states it, or null when this deployment does not
 *  serve spaces over MCP (`MCP_ACCESS_ENABLED=false` answers 404). Never built
 *  in the browser: the browser knows the API by the address this page was
 *  configured with, which is not always the one outside clients reach, and a
 *  link built here would be offered even where there is nothing behind it.
 *  Anything else that goes wrong throws: a failed request is not the feature
 *  being off, and hiding the section for it would look exactly like it. */
export async function fetchMcpUrl(apiUrl: string, podId: string, fetcher: typeof fetch = fetch): Promise<string | null> {
    const response = await fetcher(apiUrl + "/oauth/mcp-endpoint/" + encodeURIComponent(podId), {
        credentials: "include",
        headers: { Accept: "application/json" },
    });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error("The link could not be loaded (" + response.status + ").");
    const body = (await response.json()) as { url?: unknown };
    if (typeof body.url !== "string") throw new Error("The link could not be loaded.");
    return body.url;
}

/** Ends the connection and every token it was given; the app has to ask
 *  again to come back. A 404 means it is already gone, which is the outcome
 *  asked for. */
export async function disconnectClient(apiUrl: string, grantId: string, fetcher: typeof fetch = fetch): Promise<void> {
    const response = await fetcher(apiUrl + "/oauth/grants/" + encodeURIComponent(grantId), {
        method: "DELETE",
        credentials: "include",
    });
    if (!response.ok && response.status !== 404) throw new Error("It could not be disconnected (" + response.status + ").");
}
