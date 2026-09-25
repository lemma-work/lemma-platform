import { useCallback, useEffect, useRef, useState } from "react";
import { desktopBridgeAvailable, invoke } from "./bridge";
import { thisComputer } from "./this-computer";

/** How the desktop shell reports the sandbox image download.
 *
 *  The image a pod runs its work in is several hundred megabytes. It is fetched
 *  behind a workspace the user is already in rather than inside startup, and
 *  this is how they find out it is happening. */
export type SandboxImageState =
    | "pending"
    | "downloading"
    | "ready"
    | "failed"
    /** No guest to warm: a supervisor-mode stack manages no sandbox images. */
    | "unsupported"
    /** A guest could hold the image and nobody has asked for it. Terminal and
     *  silent: only a download somebody asked for is worth announcing. */
    | "not-prepared"
    | "unknown";

export interface SandboxImageStatus {
    state: SandboxImageState;
    detail: string;
}

/** What, if anything, to show for a transition. */
export type SandboxImageNotice =
    | { kind: "none" }
    | { kind: "downloading" | "ready" | "unavailable"; title: string; description: string };

const NOTHING: SandboxImageNotice = { kind: "none" };

/** The notice this transition earns.
 *
 *  The rule that matters is the last: a workspace that was already warm when
 *  the page opened says nothing at all. "Sandbox ready" to someone who never
 *  saw it downloading is a notification about nothing, on every reload for the
 *  life of the install. */
export function sandboxImageNotice(previous: SandboxImageState | null, next: SandboxImageStatus): SandboxImageNotice {
    if (previous === next.state) return NOTHING;
    if (next.state === "downloading") {
        return {
            kind: "downloading",
            title: "Preparing the workspace sandbox",
            description: next.detail || "Downloading the image teammates run their work in.",
        };
    }
    /* Both endings are only worth reporting to someone who saw the beginning. */
    if (previous !== "downloading") return NOTHING;
    if (next.state === "ready") {
        return {
            kind: "ready",
            title: "Workspace sandbox ready",
            description: `Teammates can run code, shells and browsers on ${thisComputer()}.`,
        };
    }
    if (next.state === "failed") {
        return {
            kind: "unavailable",
            title: "The sandbox will download later",
            description: next.detail || "Lemma is ready; the first task that needs it will fetch it.",
        };
    }
    return NOTHING;
}

/** Only while the answer can still change. `unsupported` is as terminal as
 *  `ready`; treating it as undecided asked every two seconds for ever. */
export function shouldKeepPolling(state: SandboxImageState | null): boolean {
    return state === null || state === "pending" || state === "downloading";
}

const KNOWN: readonly SandboxImageState[] = ["pending", "downloading", "ready", "failed", "unsupported", "not-prepared"];

export function readSandboxImageStatus(value: unknown): SandboxImageStatus {
    const record = (value ?? {}) as Record<string, unknown>;
    return {
        state: KNOWN.includes(record.state as SandboxImageState) ? (record.state as SandboxImageState) : "unknown",
        detail: typeof record.detail === "string" ? record.detail : "",
    };
}

const POLL_INTERVAL_MS = 2_000;

/** The sandbox download as one notice, polled until it has an ending.
 *
 *  Polled rather than pushed: the workspace is a remote origin and its
 *  capability grants named commands, not the event channel. Each poll is a
 *  lock read in the shell. A finished notice stays until dismissed; a
 *  download in progress cannot be dismissed into silence, only hidden. */
export function useSandboxImageNotice(): { notice: SandboxImageNotice; dismiss: () => void } {
    const previous = useRef<SandboxImageState | null>(null);
    const [notice, setNotice] = useState<SandboxImageNotice>(NOTHING);

    useEffect(() => {
        if (!desktopBridgeAvailable()) return;
        let cancelled = false;
        let timer: number | undefined;

        const schedule = () => {
            if (cancelled || !shouldKeepPolling(previous.current)) return;
            timer = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);
        };

        const tick = async () => {
            let status: SandboxImageStatus;
            try {
                status = readSandboxImageStatus(await invoke("sandbox_image_status"));
            } catch {
                /* The shell is there and would not answer. Nothing worth a
                   notice of its own; ask again next tick. */
                schedule();
                return;
            }
            if (cancelled) return;
            const next = sandboxImageNotice(previous.current, status);
            previous.current = status.state;
            if (next.kind !== "none") setNotice(next);
            schedule();
        };

        void tick();
        return () => {
            cancelled = true;
            if (timer !== undefined) window.clearTimeout(timer);
        };
    }, []);

    const dismiss = useCallback(() => setNotice(NOTHING), []);
    return { notice, dismiss };
}
