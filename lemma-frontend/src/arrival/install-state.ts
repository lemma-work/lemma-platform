/** Where each picked app is, from the server's install queue, in the words
 *  the way in uses. Pure, for the hook in `install-run.ts` and its tests. */

import { sourceOfInstall } from "./install-plan";
import type { RunKind } from "./stage-model";

/** `GET /pods/{pod_id}/bundle/installs/{install_id}`, as far as it is read. */
export interface ServerInstall {
    install_id: string;
    finished: boolean;
    plugins: {
        name: string;
        path?: string | null;
        position: number;
        state: "WAITING" | "INSTALLING" | "INSTALLED" | "NEEDS_ANSWER" | "FAILED" | "CANCELLED" | string;
        app?: string | null;
        reason?: string | null;
        progress?: { done: number; total: number } | null;
        current_step?: { kind: string; name: string } | null;
    }[];
}

export interface RunRow {
    kind: RunKind;
    progress: number;
    step: string | null;
    app: string | null;
    reason: string | null;
}

/** Each picked plugin's row, matched to the server's by the folder it was
 *  installed from. Before the server answers, the picks are waiting; a pick
 *  with no folder to install from was never sent, and asks for its page. */
export function runOf(
    plugins: readonly { slug: string; install: string | null }[],
    server: ServerInstall | null,
    refused: string | null,
): Record<string, RunRow> {
    const rows: Record<string, RunRow> = {};
    for (const plugin of plugins) {
        const source = plugin.install ? sourceOfInstall(plugin.install) : null;
        if (!source) {
            rows[plugin.slug] = { kind: "asks", progress: 0, step: null, app: null, reason: "It installs from its own page." };
            continue;
        }
        if (refused) {
            rows[plugin.slug] = { kind: "failed", progress: 0, step: null, app: null, reason: refused };
            continue;
        }
        const item = server?.plugins.find((one) => (one.path ?? null) === (source.path ?? null)) ?? null;
        if (!item) {
            rows[plugin.slug] = { kind: "waiting", progress: 0, step: null, app: null, reason: null };
            continue;
        }
        const total = item.progress?.total ?? 0;
        const share = total > 0 ? (item.progress?.done ?? 0) / total : 0;
        const kind: RunKind = item.state === "INSTALLED" ? "added"
            : item.state === "INSTALLING" ? "adding"
                : item.state === "WAITING" ? "waiting"
                    : item.state === "NEEDS_ANSWER" ? "asks"
                        : "failed";
        rows[plugin.slug] = {
            kind,
            progress: kind === "added" ? 1 : share,
            step: item.current_step?.kind ?? null,
            app: item.app ?? null,
            reason: item.reason ?? null,
        };
    }
    return rows;
}

/** What the step under way is doing, said the way the Lemma would say it. */
export function stepWords(kind: string | null): string {
    switch ((kind ?? "").toUpperCase()) {
        case "TABLE":
        case "FILE":
        case "FOLDER":
            return "Making space for its information";
        case "FUNCTION":
        case "AGENT":
        case "SKILL":
        case "FUNCTION_GRANTS":
        case "AGENT_GRANTS":
            return "Learning how it works";
        case "APP":
            return "Putting its app together";
        case "WORKFLOW":
        case "SCHEDULE":
        case "TRIGGER":
            return "Setting up what it does on its own";
        case "CONNECTOR":
            return "Checking the accounts it reads";
        default:
            return "Getting it ready";
    }
}
