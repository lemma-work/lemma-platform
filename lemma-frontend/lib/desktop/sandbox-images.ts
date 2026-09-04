'use client';

import { useEffect, useRef } from 'react';
import { toast } from 'sonner';

import { isLocalDeployment } from '@/lib/config';
import { thisComputer } from '@/lib/desktop/this-computer';

/**
 * How the desktop shell reports the sandbox image download.
 *
 * The image a pod runs its work in is several hundred megabytes. It used to be
 * fetched inside startup, which held "Lemma is ready" behind a download nobody
 * had asked for yet; it now runs behind a workspace the user is already in, and
 * this is how they find out it is happening.
 */
export type SandboxImageState =
    | 'pending'
    | 'downloading'
    | 'ready'
    | 'failed'
    /** No guest to warm — a supervisor-mode stack manages no sandbox images. */
    | 'unsupported'
    | 'unknown';

export type SandboxImageStatus = {
    state: SandboxImageState;
    detail: string;
};

/** What, if anything, to show for this transition. */
export type SandboxImageNotice =
    | { kind: 'none' }
    | { kind: 'downloading'; title: string; description: string }
    | { kind: 'ready'; title: string; description: string }
    | { kind: 'unavailable'; title: string; description: string };

const NOTHING: SandboxImageNotice = { kind: 'none' };

/**
 * The toast this transition earns.
 *
 * The rule that matters is the last one: a workspace that was already warm
 * when the page opened says nothing at all. Announcing "sandbox ready" to
 * someone who never saw it downloading is a notification about nothing, and it
 * would fire on every reload for the rest of the install's life.
 */
export function sandboxImageNotice(
    previous: SandboxImageState | null,
    next: SandboxImageStatus,
): SandboxImageNotice {
    if (previous === next.state) return NOTHING;

    if (next.state === 'downloading') {
        return {
            kind: 'downloading',
            title: 'Preparing the workspace sandbox',
            description:
                next.detail || 'Downloading the image pods run their work in.',
        };
    }

    // Both endings are only worth reporting to someone who saw the beginning.
    if (previous !== 'downloading') return NOTHING;

    if (next.state === 'ready') {
        return {
            kind: 'ready',
            title: 'Workspace sandbox ready',
            description: `Pods can run code, shells, and browsers on ${thisComputer()}.`,
        };
    }
    if (next.state === 'failed') {
        return {
            kind: 'unavailable',
            title: 'Workspace sandbox will download later',
            description:
                next.detail || 'Lemma is ready; the first task in a pod will fetch it.',
        };
    }
    return NOTHING;
}

/**
 * Is there any point asking again?
 *
 * Only while the answer can still change. `unsupported` is as terminal as
 * `ready`: a stack with no guest to warm will never report anything else, and
 * treating it as "not decided yet" left the workspace asking every two seconds
 * for the rest of the session.
 */
export function shouldKeepPolling(state: SandboxImageState | null): boolean {
    return state === null || state === 'pending' || state === 'downloading';
}

function readStatus(value: unknown): SandboxImageStatus {
    const record = (value ?? {}) as Record<string, unknown>;
    const state = record.state;
    const known: readonly SandboxImageState[] = [
        'pending',
        'downloading',
        'ready',
        'failed',
        'unsupported',
    ];
    return {
        state: known.includes(state as SandboxImageState)
            ? (state as SandboxImageState)
            : 'unknown',
        detail: typeof record.detail === 'string' ? record.detail : '',
    };
}

const POLL_INTERVAL_MS = 2000;

/**
 * Show the sandbox image download as a toast, and stop once it has an ending.
 *
 * Polled rather than pushed: the workspace runs on a remote origin and its
 * capability grants named commands only, not the event channel. The poll costs
 * a lock read in the shell — locald has already pushed the state there — and
 * stops as soon as the answer can no longer change.
 */
export function useSandboxImageToasts(): void {
    const previous = useRef<SandboxImageState | null>(null);
    const loadingToast = useRef<string | number | null>(null);

    useEffect(() => {
        if (typeof window === 'undefined') return;
        const invoke = window.__TAURI__?.core?.invoke;
        if (typeof invoke !== 'function' || !isLocalDeployment()) return;

        let cancelled = false;
        let timer: number | undefined;

        const dismissLoading = () => {
            if (loadingToast.current !== null) {
                toast.dismiss(loadingToast.current);
                loadingToast.current = null;
            }
        };

        const tick = async () => {
            let status: SandboxImageStatus;
            try {
                status = readStatus(await invoke('sandbox_image_status'));
            } catch {
                // The shell is there but would not answer. Nothing here is
                // worth a toast of its own; try again on the next tick.
                schedule();
                return;
            }
            if (cancelled) return;

            const notice = sandboxImageNotice(previous.current, status);
            previous.current = status.state;

            if (notice.kind === 'downloading') {
                loadingToast.current = toast.loading(notice.title, {
                    description: notice.description,
                    duration: Infinity,
                });
            } else if (notice.kind === 'ready') {
                dismissLoading();
                toast.success(notice.title, { description: notice.description });
            } else if (notice.kind === 'unavailable') {
                dismissLoading();
                toast.info(notice.title, { description: notice.description });
            }

            schedule();
        };

        const schedule = () => {
            if (cancelled || !shouldKeepPolling(previous.current)) return;
            timer = window.setTimeout(() => void tick(), POLL_INTERVAL_MS);
        };

        void tick();
        return () => {
            cancelled = true;
            if (timer !== undefined) window.clearTimeout(timer);
            dismissLoading();
        };
    }, []);
}
