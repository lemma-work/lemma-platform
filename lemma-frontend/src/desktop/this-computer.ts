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

export interface DescribedStatus {
    label: string;
    detail: string;
    tone: Tone;
    /** Whether "Try again" means anything here. */
    retry: boolean;
    /** Whether the host itself stopped and needs starting again. */
    restart?: boolean;
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
 *  leave is a failure wearing its clothes.
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
): DescribedStatus {
    if (!status) {
        /* In a hosted workspace the first poll is the one that has to start
           locald, so "nothing yet" is the normal opening state. */
        return error
            ? { label: "Unavailable", detail: error, tone: "warn", retry: false }
            : { label: "Checking", detail: `Asking ${noun} which agents it can run.`, tone: "muted", retry: false };
    }
    if (!status.available) {
        return {
            label: "Not available",
            detail: "This build of Lemma does not include the Agent Host.",
            tone: "muted",
            retry: false,
        };
    }
    /* Before anything about the connection: a host that keeps exiting is
       not "starting", however long the page waits. */
    if (status.restart_circuit_open) {
        return {
            label: "Stopped working",
            detail: status.last_error ?? `The Agent Host on ${noun} kept stopping. Restart it, or open its log to see why.`,
            tone: "warn",
            retry: false,
            restart: true,
        };
    }
    const target = selectWorkspaceTarget(status.targets, workspaceUrl, userId);
    if (!target) {
        /* Nothing retries on its own — one attempt per page, so a machine that
           cannot pair does not mint pairing codes in a loop — so "Connecting"
           after a failure was a claim that stayed on screen indefinitely. */
        if (connectError) return { label: "Couldn’t connect", detail: connectError, tone: "warn", retry: true };
        return {
            label: "Connecting",
            detail: `Setting ${noun} up to run Claude Code, Codex and other local agents for this workspace.`,
            tone: "muted",
            retry: false,
        };
    }
    if (!status.running) {
        return { label: "Starting", detail: `Bringing ${noun} online for this workspace.`, tone: "muted", retry: false };
    }
    if (target.connection_state === "ONLINE") {
        const runs = target.active_runs ?? 0;
        return {
            label: "Connected",
            detail: runs > 0 ? `Running ${runs} ${runs === 1 ? "task" : "tasks"} now.` : "Ready for work.",
            tone: "ok",
            retry: false,
        };
    }
    /* The tray's distinction, in the tray's words: a failed last attempt is
       not a reconnection in progress. */
    const failure = target.last_error || status.last_error;
    return failure
        ? { label: "Unreachable", detail: failure, tone: "warn", retry: false }
        : { label: "Reconnecting", detail: "Trying to reach this workspace.", tone: "warn", retry: false };
}
