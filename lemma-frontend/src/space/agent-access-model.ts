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
