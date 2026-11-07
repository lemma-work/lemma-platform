/** The machines a person can reach from a teammate's space, and what to say
 *  about them.
 *
 *  Pure readings of what `/machines` answers, kept apart from the screen for
 *  the reason the rest of this folder is: a rule can be tested, a pane cannot.
 */

import { readableSize } from "./machine";

export type MachineKind = "default" | "pod" | "connected";
export type MachineStatus = "running" | "starting" | "stopped" | "offline" | "error" | "deleted";
export type MachineAccess = "status" | "use" | "manage";
export type MachineTemplate = "workspace" | "workspace-docker";

export interface MachineCapabilities {
    processes: boolean;
    pty: boolean;
    python: boolean;
    files: boolean;
    previews: boolean;
    browser: boolean;
    docker: boolean;
}

/** One machine, exactly as `GET /machines` sends it. */
export interface Machine {
    id: string;
    ref: string;
    kind: MachineKind;
    name: string;
    display_name: string;
    template: MachineTemplate;
    size: string;
    fabric: string;
    capabilities: MachineCapabilities;
    visibility: "private" | "pod" | "restricted";
    status: MachineStatus;
    access: MachineAccess;
    run_slots: number;
    home: string;
    pod_id?: string | null;
    last_used_at?: string | null;
    compute_units_this_month?: number | null;
}

export interface MachinePolicy {
    default_size: string;
    templates: { template: MachineTemplate; sizes: string[] }[];
    max_pod_machines?: number | null;
    max_connected_machines?: number | null;
    max_running?: number | null;
}

export type ProcessState =
    | "reserved" | "starting" | "unknown" | "running"
    | "succeeded" | "failed" | "cancelled" | "timed_out";

export interface MachineProcess {
    id: string;
    command: string;
    cwd: string;
    state: ProcessState;
    exit_code?: number | null;
    run_id?: string | null;
    service?: string | null;
    started_at?: string | null;
    started_by?: string | null;
    tty?: boolean;
}

export interface Preview {
    id: string;
    port: number;
    visibility: "private" | "link";
    url: string;
    created_at: string;
    expires_at?: string | null;
    revoked_at?: string | null;
}

export type ResetScope = "processes" | "runs" | "caches" | "docker" | "browser-session" | "home";

export interface ResetResult {
    scope: ResetScope;
    freed_bytes?: number | null;
    detail?: string | null;
}

/** The path segment for a machine's `ref`.
 *
 *  The server names a pod machine `pod:<pod>/<name>` and asks for `:` in place
 *  of the slash in a URL path — a slash there would be read as the next
 *  segment. Only the first, because the name after it is the machine's own. */
export function pathRef(ref: string): string {
    return encodeURIComponent(ref.replace("/", ":"));
}

export interface Rail {
    mine: Machine[];
    pod: Machine[];
    connected: Machine[];
}

/** The three groups the rail shows, in the order it shows them. Deleted
 *  machines are not on it: the list may still answer with one for a moment
 *  after it went, and a row for it would offer controls that cannot work. */
export function groupMachines(machines: readonly Machine[]): Rail {
    const live = machines.filter((machine) => machine.status !== "deleted");
    const byName = (a: Machine, b: Machine) => (a.display_name || a.name).localeCompare(b.display_name || b.name);
    return {
        mine: live.filter((machine) => machine.kind === "default"),
        pod: live.filter((machine) => machine.kind === "pod").sort(byName),
        connected: live.filter((machine) => machine.kind === "connected").sort(byName),
    };
}

/** `2x4` as a person reads it. Anything else is shown as sent. */
export function sizeLabel(size: string): string {
    const match = /^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$/.exec(size);
    if (!match) return size;
    return `${match[1]} vCPU · ${match[2]} GB`;
}

/** Compute this month, one decimal. Null when the server did not count it,
 *  which is not the same as none. */
export function computeLabel(units: number | null | undefined): string | null {
    if (units === null || units === undefined || !Number.isFinite(units)) return null;
    return `${units.toFixed(1)} CU`;
}

/** The one line under the rail: how much compute these machines used this
 *  month, and how many of them are running now. */
export function usageSummary(machines: readonly Machine[]): string {
    const counted = machines.filter((machine) => typeof machine.compute_units_this_month === "number");
    const running = machines.filter((machine) => machine.status === "running").length;
    const parts: string[] = [];
    if (counted.length > 0) {
        const total = counted.reduce((sum, machine) => sum + (machine.compute_units_this_month ?? 0), 0);
        parts.push(`${total.toFixed(1)} CU this month`);
    }
    parts.push(running === 1 ? "1 running" : `${running} running`);
    return parts.join(" · ");
}

export const STATUS_WORD: Record<MachineStatus, string> = {
    running: "Running",
    starting: "Starting",
    stopped: "Asleep",
    offline: "Offline",
    error: "Needs attention",
    deleted: "Deleted",
};

/** The scopes Clean up offers for this machine. Docker only where the
 *  machine has it; home never — that is its own, separately confirmed act. */
export function cleanupScopes(capabilities: MachineCapabilities | undefined): ResetScope[] {
    const scopes: ResetScope[] = ["processes", "runs", "caches"];
    if (capabilities?.docker) scopes.push("docker");
    scopes.push("browser-session");
    return scopes;
}

export const SCOPE_WORDS: Record<ResetScope, { label: string; note: string }> = {
    processes: { label: "Stop processes", note: "Every process and service running on it." },
    runs: { label: "Clear run folders", note: "Scratch folders left behind by finished runs." },
    caches: { label: "Clear caches", note: "Package and build caches. They fill again on use." },
    docker: { label: "Prune Docker", note: "Stopped containers, unused images and build cache." },
    "browser-session": { label: "Restart the browser", note: "Closes its tabs. Logins are kept." },
    home: { label: "Reset home", note: "Replaces the disk with a fresh one." },
};

/** One line per scope that ran: what it was and what it gave back. */
export function resetLines(results: readonly ResetResult[]): string[] {
    return results.map((result) => {
        const label = SCOPE_WORDS[result.scope]?.label ?? result.scope;
        const freed = typeof result.freed_bytes === "number" && result.freed_bytes > 0
            ? `freed ${readableSize(result.freed_bytes)}`
            : null;
        return [label, freed, result.detail || null].filter(Boolean).join(" — ");
    });
}

/** Everything a clean-up gave back, across scopes. */
export function totalFreed(results: readonly ResetResult[]): number {
    return results.reduce((sum, result) => sum + (typeof result.freed_bytes === "number" ? Math.max(0, result.freed_bytes) : 0), 0);
}

/** Whether a process has stopped for good. */
export function isFinished(state: ProcessState): boolean {
    return state === "succeeded" || state === "failed" || state === "cancelled" || state === "timed_out";
}

/** What the process stream sends, read defensively: anything that does not
 *  parse as one of the two shapes is dropped rather than written to the
 *  terminal. */
export type StreamMessage =
    | { type: "output"; sequence: number; channel: string; data: string }
    | { type: "exit"; state: string; exit_code: number | null };

export function parseStreamMessage(raw: unknown): StreamMessage | null {
    if (typeof raw !== "string") return null;
    let message: unknown;
    try {
        message = JSON.parse(raw);
    } catch {
        return null;
    }
    if (!message || typeof message !== "object") return null;
    const value = message as Record<string, unknown>;
    if (value.type === "output" && typeof value.data === "string") {
        return {
            type: "output",
            sequence: typeof value.sequence === "number" ? value.sequence : 0,
            channel: typeof value.channel === "string" ? value.channel : "stdout",
            data: value.data,
        };
    }
    if (value.type === "exit") {
        return {
            type: "exit",
            state: typeof value.state === "string" ? value.state : "unknown",
            exit_code: typeof value.exit_code === "number" ? value.exit_code : null,
        };
    }
    return null;
}

export function inputMessage(data: string): string {
    return JSON.stringify({ type: "input", data });
}

export function resizeMessage(cols: number, rows: number): string {
    return JSON.stringify({ type: "resize", cols, rows });
}

/** The socket for one process's output, authenticated the way the browser
 *  view is: cookies where this page shares a site with the API, the bearer in
 *  the query where it has one. `after` resumes past what was already shown. */
export function processStreamUrl(api: string, ref: string, processId: string, options: {
    accessToken?: string | null;
    after?: number;
} = {}): string {
    const base = api.replace(/^http/, "ws").replace(/\/$/, "");
    const query = new URLSearchParams();
    if (options.after && options.after > 0) query.set("after", String(options.after));
    if (options.accessToken) query.set("access_token", options.accessToken);
    const search = query.toString();
    return `${base}/machines/${pathRef(ref)}/processes/${encodeURIComponent(processId)}/stream${search ? "?" + search : ""}`;
}

/** Pod machine names the server will take: short, lower-case, a slug. */
export function validMachineName(name: string): boolean {
    return /^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$/.test(name);
}

/** Choices for how long a link preview lives. A link is public, so it is
 *  capped at a day by the server; private previews may stay open. */
export const LINK_TTLS: { label: string; seconds: number }[] = [
    { label: "1 hour", seconds: 3_600 },
    { label: "8 hours", seconds: 8 * 3_600 },
    { label: "24 hours", seconds: 24 * 3_600 },
];

/** A preview that still answers: not revoked, not expired. */
export function previewLive(preview: Preview, now: number = Date.now()): boolean {
    if (preview.revoked_at) return false;
    if (preview.expires_at && new Date(preview.expires_at).getTime() <= now) return false;
    return true;
}
