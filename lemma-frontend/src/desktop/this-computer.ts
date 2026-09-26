import { useSyncExternalStore } from "react";
import type { AgentHostStatus, AgentHostTarget } from "./agent-host";

/* ── what to call it ───────────────────────────────────────────────── */

/** What to call the machine Lemma is running on.
 *
 *  The product ships a Windows build, so "this Mac" is wrong often enough to
 *  matter — and most wrong on the screen whose whole job is explaining why
 *  nothing was found on it. `platform` from the shell when there is one, since
 *  it cannot be wrong; the user agent otherwise. */
export type ComputerNoun = "this Mac" | "this PC" | "this computer";

export function thisComputer(): ComputerNoun {
    if (typeof window !== "undefined") {
        const platform = window.__LEMMA_DESKTOP__?.platform;
        if (platform === "macos") return "this Mac";
        if (platform === "windows") return "this PC";
        if (platform) return "this computer";
    }
    if (typeof navigator === "undefined") return "this computer";
    /* `userAgentData` is the modern answer and what Chromium populates;
       `platform` is deprecated but is what WKWebView reports. */
    const data = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData;
    const signal = `${data?.platform ?? ""} ${navigator.platform ?? ""} ${navigator.userAgent ?? ""}`;
    if (/mac/i.test(signal)) return "this Mac";
    if (/win/i.test(signal)) return "this PC";
    return "this computer";
}

/** Sentence-initial: "This Mac", "This PC". */
export function capitalised(noun: ComputerNoun): string {
    return noun.charAt(0).toUpperCase() + noun.slice(1);
}

function subscribeNothing(): () => void {
    return () => {};
}

/** The noun, safe to render. The server has neither `navigator` nor the shell's
 *  globals, so it renders the neutral word and the specific one arrives on the
 *  commit after hydration — the two renders agree, and the noun still ends up
 *  right. */
export function useThisComputer(): ComputerNoun {
    return useSyncExternalStore(subscribeNothing, thisComputer, () => "this computer");
}

/* ── which pairing is this workspace's ─────────────────────────────── */

function originOf(url: string | null): string | null {
    if (!url) return null;
    try {
        return new URL(url).origin;
    } catch {
        return null;
    }
}

/** The pairing that belongs to the workspace on screen, if any.
 *
 *  A computer can be paired to several workspaces at once — that is what
 *  `targets` is. Reading the first one, or trusting `paired` ("paired to
 *  anything"), described a Mac's local pairing while a hosted workspace was on
 *  screen. The automatic connection asks the same question and must get the
 *  same answer. A target with no URL matches nothing: it cannot be shown to be
 *  this workspace's.
 *
 *  Nor does a pairing the host turned off (`enabled: false`), which serves
 *  nobody, or one that belongs to somebody other than `userId`: two people
 *  signing in to the app on one Mac are two people, and the second one's runs
 *  must not go to the first one's pairing. An older shell reports no
 *  `user_id`; its pairing is taken as the signed-in person's. */
export function selectWorkspaceTarget(
    targets: readonly AgentHostTarget[],
    workspaceUrl: string | null,
    userId: string | null = null,
): AgentHostTarget | null {
    const workspace = originOf(workspaceUrl);
    if (!workspace) return null;
    return (
        targets.find(
            (target) =>
                originOf(target.url) === workspace &&
                target.enabled !== false &&
                (!userId || !target.user_id || target.user_id === userId),
        ) ?? null
    );
}

/* ── one reported state ────────────────────────────────────────────── */

export type Tone = "ok" | "warn" | "muted";

/** What the card offers beside a state, when there is something to do.
 *
 *  - `retry`: a connection attempt failed; ask again.
 *  - `reconnect`: this computer is not connected here and nothing will
 *    connect it on its own; "Connect again", which may turn back on a
 *    computer that was removed from the account.
 *  - `restart`: the background service stopped and is not coming back by
 *    itself; starting it deliberately also forgives its past crashes.
 *  - `update`: this copy of Lemma is too old, or too bare, for the job. */
export type StatusAction = "retry" | "reconnect" | "restart" | "update";

export interface DescribedStatus {
    /** What the card offers beside this state, if anything. */
    action: StatusAction | null;
    label: string;
    detail: string;
    tone: Tone;
    /** Whether "Try again" means anything here. */
    retry: boolean;
}

/** How long a stage on the way up may last before it is called what it is.
 *
 *  Pairing, a spawn and a first connection each take seconds. Half a minute
 *  of any of them is not a slow start but one that is not happening, and the
 *  card says so rather than leaving "Connecting" on screen for the rest of
 *  the day. */
export const STALLED_AFTER_MS = 30_000;

/** The shell's refusal, said without its plumbing. The full text is in the
 *  log, which the card offers beside it. */
export function plainHostError(message: string, noun: ComputerNoun = "this computer"): string {
    if (/not allowed by ACL|only the Lemma workspace on/i.test(message)) {
        return `${capitalised(noun)} can only be checked from Lemma’s own window.`;
    }
    return "Lemma’s agent service isn’t responding. Restart Lemma.";
}

/** Why pairing failed, in words a person can act on. Lemma's own refusal of
 *  a removed computer is already one sentence of that kind and is kept; the
 *  rest is the sidecar's stderr, which belongs in the log. */
export function plainConnectError(message: string, noun: ComputerNoun = "this computer"): string {
    if (/was removed from this account/i.test(message)) return message.replace(/^Error:\s*/, "");
    if (/not allowed by ACL|only the Lemma workspace on/i.test(message)) return plainHostError(message, noun);
    return `Lemma couldn’t connect ${noun} to this workspace. Try again, or open the log to see why.`;
}

/** A workspace that has moved on to a newer protocol than this app speaks.
 *  The host notes it on the pairing and stops trying; only an update helps. */
export function needsUpdate(failure: string | null | undefined): boolean {
    return Boolean(failure && /needs a newer Agent Host|newer Agent Host protocol|Agent Host protocol \d+ is unsupported|upgrade required/i.test(failure));
}

/** The three status planes, ranked into one state.
 *
 *  `docs/architecture/agent-host.md` ("The three status planes") has the
 *  reasoning: the card reports reachability, not liveness, in the order
 *
 *      not available → connecting → starting → connected → unreachable → reconnecting
 *
 *  with "couldn't connect" displacing *connecting*. Every state is a report,
 *  never a prompt — there is no Connect or Turn on for this computer — and
 *  every optimistic one can stop being optimistic: a stage a thing cannot
 *  leave is a failure wearing its clothes. So *connecting* becomes "Not
 *  connected" and *starting* becomes "Not running" once `stalled` says it
 *  has lasted longer than {@link STALLED_AFTER_MS}, and *starting* does at
 *  once when the supervisor has stopped restarting the service.
 *
 *  `error` is the shell refusing to answer *about* the host; `connectError` is
 *  the host answering fine and the connection itself failing. */
export function describeThisComputer(
    status: AgentHostStatus | null,
    error: string | null,
    workspaceUrl: string | null,
    connectError: string | null,
    noun: ComputerNoun = "this computer",
    userId: string | null = null,
    stalled = false,
): DescribedStatus {
    const Noun = capitalised(noun);
    if (!status) {
        /* In a hosted workspace the first poll is the one that has to start
           locald, so "nothing yet" is the normal opening state. */
        return error
            ? { label: "Unavailable", detail: plainHostError(error, noun), tone: "warn", retry: false, action: null }
            : {
                label: "Checking",
                detail: `Asking ${noun} which agents it can run.`,
                tone: "muted",
                retry: false,
                action: null,
            };
    }
    if (!status.available) {
        /* Not a dead end: a build without coding agents is fixed by the build
           that has them. */
        return {
            label: "Not available",
            detail: `This copy of Lemma can’t run coding agents on ${noun}. Update Lemma to get them.`,
            tone: "muted",
            retry: false,
            action: "update",
        };
    }
    const target = selectWorkspaceTarget(status.targets, workspaceUrl, userId);
    if (!target) {
        /* Nothing retries on its own — one attempt per page, so a machine that
           cannot pair does not mint pairing codes in a loop — so "Connecting"
           after a failure was a claim that stayed on screen indefinitely. The
           same is true of a pairing that vanished after this page's one
           attempt was spent: the host drops a pairing Lemma keeps refusing. */
        if (connectError) {
            return {
                label: "Couldn’t connect",
                detail: plainConnectError(connectError, noun),
                tone: "warn",
                retry: true,
                action: "retry",
            };
        }
        if (stalled) {
            return {
                label: "Not connected",
                detail: `${Noun} isn’t connected to this workspace, and won’t connect on its own.`,
                tone: "warn",
                retry: true,
                action: "reconnect",
            };
        }
        return {
            label: "Connecting",
            detail: `Setting ${noun} up to run Claude Code, Codex and other local agents for this workspace.`,
            tone: "muted",
            retry: false,
            action: null,
        };
    }
    if (!status.running) {
        /* A spawn takes seconds; half a minute of "Starting" is a service
           that is not coming up, and only a deliberate start helps. */
        if (stalled) {
            return {
                label: "Not running",
                detail: `Lemma’s coding-agent service on ${noun} stopped and isn’t restarting on its own.`,
                tone: "warn",
                retry: false,
                action: "restart",
            };
        }
        return { label: "Starting", detail: `Bringing ${noun} online for this workspace.`, tone: "muted", retry: false, action: null };
    }
    if (target.connection_state === "ONLINE") {
        const runs = target.active_runs ?? 0;
        return {
            label: "Connected",
            detail: runs > 0 ? `Running ${runs} ${runs === 1 ? "task" : "tasks"} now.` : "Ready for work.",
            tone: "ok",
            retry: false,
            action: null,
        };
    }
    /* The tray's distinction, in the tray's words: a failed last attempt is
       not a reconnection in progress. */
    const failure = target.last_error || status.last_error;
    if (needsUpdate(failure)) {
        return {
            label: "Update needed",
            detail: `This workspace needs a newer Lemma app to run coding agents on ${noun}.`,
            tone: "warn",
            retry: false,
            action: "update",
        };
    }
    return failure
        ? {
            label: "Unreachable",
            detail: "Can’t reach this workspace right now. Lemma keeps trying; the log says why.",
            tone: "warn",
            retry: false,
            action: null,
        }
        : { label: "Reconnecting", detail: "Trying to reach this workspace.", tone: "warn", retry: false, action: null };
}
