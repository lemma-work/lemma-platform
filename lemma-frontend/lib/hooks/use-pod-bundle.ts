'use client';

/**
 * Data layer for the pod bundle share/import/export experience.
 *
 * Mirrors `app/modules/pod_bundle/api/schemas.py`. The backend runs export /
 * import / publish as streaq jobs and keeps ephemeral state in Redis; these
 * helpers start a job (202) and then *poll* the Redis-backed status endpoint
 * until it reaches a terminal state. Polling is the design's first-class
 * progress path (SSE is an optimization we can layer on later).
 */

import { parseSSEJson, readSSE } from 'lemma-sdk';

import { getLemmaApiBaseUrl, getLemmaClient } from '@/lib/sdk/lemma-client';

// ---------------------------------------------------------------------------
// Types (kept in sync with the backend response models)
// ---------------------------------------------------------------------------

export type ExportStatus = 'QUEUED' | 'EXPORTING' | 'READY' | 'FAILED';
export type ImportStatus =
    | 'QUEUED'
    | 'FETCHING'
    | 'PLANNING'
    | 'AWAITING_CONFIRMATION'
    | 'APPLYING'
    | 'COMPLETED'
    | 'FAILED'
    | 'CANCELLED'
    | 'PARTIALLY_CANCELLED';
export type PublishStatus = 'QUEUED' | 'EXPORTING' | 'PUBLISHING' | 'COMPLETED' | 'FAILED';
export type StepAction = 'CREATE' | 'UPDATE' | 'SKIP';
export type StepStatus = 'PENDING' | 'RUNNING' | 'DONE' | 'FAILED' | 'SKIPPED';
export type BundleSourceKind = 'URL' | 'GITHUB';
export type PublishMode = 'CREATE' | 'UPDATE';

export interface BundleProgress {
    done: number;
    total: number;
}

export interface ExportStatusResponse {
    export_id: string;
    status: ExportStatus;
    progress: BundleProgress;
    bundle_filename: string | null;
    download_url: string | null;
    expires_at: string | null;
    warnings: string[];
    error: string | null;
}

export interface PlanStep {
    index: number;
    kind: string;
    name: string;
    action: StepAction;
    destructive: boolean;
    detail: Record<string, unknown>;
    status: StepStatus;
    error: string | null;
}

export interface VariableSpec {
    name: string;
    kind: string;
    description: string | null;
    required: boolean;
    default: string | null;
    /** For `account`-kind variables: the connector, e.g. "slack". */
    connector?: string | null;
    /** For `account`-kind variables: which of the connector's kinds the source
     * install used ("composio", "package", "mcp", ...), so the picker selects
     * or creates an account of the same kind rather than any account for that
     * connector. */
    connector_kind?: string | null;
}

export interface ImportPlan {
    format_version: number;
    bundle_name: string | null;
    steps: PlanStep[];
    variables: VariableSpec[];
    warnings: string[];
    has_destructive_steps: boolean;
}

export interface ImportStatusResponse {
    import_id: string;
    pod_id: string;
    status: ImportStatus;
    source_kind: string;
    plan: ImportPlan | null;
    progress: BundleProgress;
    events_url: string;
    error: string | null;
    error_code: string | null;
    retryable: boolean;
    warnings: string[];
}

export interface UploadResponse {
    url: string;
    expires_at: string;
}

export interface PublishStatusResponse {
    publish_id: string;
    pod_id: string;
    status: PublishStatus;
    repo_name: string;
    mode: PublishMode;
    private: boolean;
    account_id: string | null;
    repo_url: string | null;
    progress: BundleProgress;
    events_url: string;
    error: string | null;
    error_code: string | null;
    retryable: boolean;
    warnings: string[];
}

const EXPORT_TERMINAL: ExportStatus[] = ['READY', 'FAILED'];
const IMPORT_TERMINAL: ImportStatus[] = ['COMPLETED', 'FAILED', 'CANCELLED', 'PARTIALLY_CANCELLED'];
const PUBLISH_TERMINAL: PublishStatus[] = ['COMPLETED', 'FAILED'];

function client(podId: string) {
    return getLemmaClient(podId);
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export function startExport(
    podId: string,
    body: { with_data?: boolean; include?: string[] | null } = {},
): Promise<ExportStatusResponse> {
    return client(podId).request('POST', `/pods/${podId}/bundle/exports`, { body });
}

export function getExport(podId: string, exportId: string): Promise<ExportStatusResponse> {
    return client(podId).request('GET', `/pods/${podId}/bundle/exports/${exportId}`);
}

// ---------------------------------------------------------------------------
// Upload + import
// ---------------------------------------------------------------------------

export function uploadBundle(podId: string, file: File): Promise<UploadResponse> {
    const form = new FormData();
    form.append('data', file, file.name);
    return client(podId).request('POST', `/pods/${podId}/bundle/uploads`, {
        body: form,
        isFormData: true,
    });
}

export interface StartImportBody {
    kind: BundleSourceKind;
    url?: string;
    owner?: string;
    repo?: string;
    ref?: string;
    account_id?: string;
}

export function startImport(podId: string, body: StartImportBody): Promise<ImportStatusResponse> {
    return client(podId).request('POST', `/pods/${podId}/bundle/imports`, { body });
}

export function getImport(podId: string, importId: string): Promise<ImportStatusResponse> {
    return client(podId).request('GET', `/pods/${podId}/bundle/imports/${importId}`);
}

export function applyImport(
    podId: string,
    importId: string,
    body: { variables?: Record<string, string>; confirm_destructive?: boolean },
): Promise<ImportStatusResponse> {
    return client(podId).request('POST', `/pods/${podId}/bundle/imports/${importId}/apply`, { body });
}

export async function cancelImport(podId: string, importId: string): Promise<void> {
    await client(podId).request('DELETE', `/pods/${podId}/bundle/imports/${importId}`);
}

// ---------------------------------------------------------------------------
// Publish (GitHub)
// ---------------------------------------------------------------------------

export interface StartPublishBody {
    repo_name: string;
    mode?: PublishMode;
    private?: boolean;
    account_id: string;
    ai_readme?: boolean;
}

export function startPublish(podId: string, body: StartPublishBody): Promise<PublishStatusResponse> {
    return client(podId).request('POST', `/pods/${podId}/bundle/publishes`, { body });
}

export function getPublish(podId: string, publishId: string): Promise<PublishStatusResponse> {
    return client(podId).request('GET', `/pods/${podId}/bundle/publishes/${publishId}`);
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

interface PollOptions<T> {
    intervalMs?: number;
    signal?: AbortSignal;
    onTick?: (state: T) => void;
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
        if (signal?.aborted) {
            reject(new DOMException('Aborted', 'AbortError'));
            return;
        }
        const timer = setTimeout(() => {
            cleanup();
            resolve();
        }, ms);
        const onAbort = () => {
            cleanup();
            reject(new DOMException('Aborted', 'AbortError'));
        };
        const cleanup = () => {
            clearTimeout(timer);
            signal?.removeEventListener('abort', onAbort);
        };
        signal?.addEventListener('abort', onAbort, { once: true });
    });
}

async function poll<T extends { status: string }>(
    fetcher: () => Promise<T>,
    isTerminal: (state: T) => boolean,
    opts: PollOptions<T> = {},
): Promise<T> {
    const interval = opts.intervalMs ?? 1200;
    for (;;) {
        if (opts.signal?.aborted) throw new DOMException('Aborted', 'AbortError');
        const state = await fetcher();
        opts.onTick?.(state);
        if (isTerminal(state)) return state;
        await sleep(interval, opts.signal);
    }
}

export function pollExport(
    podId: string,
    exportId: string,
    opts?: PollOptions<ExportStatusResponse>,
): Promise<ExportStatusResponse> {
    return poll(() => getExport(podId, exportId), (s) => EXPORT_TERMINAL.includes(s.status), opts);
}

export function pollImport(
    podId: string,
    importId: string,
    opts?: PollOptions<ImportStatusResponse> & { until?: (s: ImportStatusResponse) => boolean },
): Promise<ImportStatusResponse> {
    const until = opts?.until;
    return poll(
        () => getImport(podId, importId),
        (s) => (until ? until(s) : IMPORT_TERMINAL.includes(s.status)),
        opts,
    );
}

export function pollPublish(
    podId: string,
    publishId: string,
    opts?: PollOptions<PublishStatusResponse>,
): Promise<PublishStatusResponse> {
    return poll(() => getPublish(podId, publishId), (s) => PUBLISH_TERMINAL.includes(s.status), opts);
}

// ---------------------------------------------------------------------------
// Realtime (SSE) — imports and publishes stream `.../events`; exports poll.
// ---------------------------------------------------------------------------

export type BundleFrame =
    | { type: 'snapshot'; seq: number; state: { status: string; progress?: BundleProgress } }
    | { type: 'status'; status: string; seq: number }
    | { type: 'step'; step: Partial<PlanStep> & { index?: number }; seq: number }
    | { type: 'progress'; done: number; total: number; seq: number }
    | { type: 'completed'; status: string; seq: number }
    | { type: 'error'; message: string; seq: number }
    | { type: 'expired' };

export interface BundleProgressView {
    status: string;
    done: number;
    total: number;
}

/**
 * Open a bundle events SSE stream and dispatch each parsed frame. Owns an abort
 * controller so the underlying fetch is cancelled on every exit — important
 * because we stop reading at non-terminal frames (e.g. AWAITING_CONFIRMATION),
 * where the server keeps the stream open. `readSSE` has no teardown of its own.
 */
export async function streamBundleEvents(
    podId: string,
    eventsUrl: string,
    onFrame: (frame: BundleFrame) => void,
    signal?: AbortSignal,
    stopWhen?: (frame: BundleFrame) => boolean,
): Promise<void> {
    const controller = new AbortController();
    const relayAbort = () => controller.abort();
    if (signal) {
        if (signal.aborted) controller.abort();
        else signal.addEventListener('abort', relayAbort, { once: true });
    }
    try {
        const stream = await client(podId).stream(eventsUrl, {
            headers: { Accept: 'text/event-stream' },
            signal: controller.signal,
        });
        for await (const raw of readSSE(stream)) {
            const frame = parseSSEJson<BundleFrame>(raw);
            if (!frame) continue;
            onFrame(frame);
            const stop = stopWhen
                ? stopWhen(frame)
                : frame.type === 'completed' || frame.type === 'error' || frame.type === 'expired';
            if (stop) return;
        }
    } finally {
        controller.abort(); // cancel the fetch/stream on any exit (stop, error, terminal)
        signal?.removeEventListener('abort', relayAbort);
    }
}

/**
 * Track a bundle job to a stopping state, driving live progress from SSE and
 * confirming the authoritative final state with a status read. Falls back to
 * polling if the stream can't be opened or drops.
 *
 * `stopStatuses` are the states that end this phase (e.g. AWAITING_CONFIRMATION
 * for a plan, COMPLETED/FAILED for an apply/publish).
 */
export async function trackBundleJob<T extends { status: string; progress?: BundleProgress }>(opts: {
    podId: string;
    eventsUrl: string;
    fetchStatus: () => Promise<T>;
    stopStatuses: string[];
    onProgress?: (view: BundleProgressView) => void;
    onFrame?: (frame: BundleFrame) => void;
    signal?: AbortSignal;
}): Promise<T> {
    const { podId, eventsUrl, fetchStatus, stopStatuses, onProgress, onFrame, signal } = opts;
    const isTerminal = (s: T) => stopStatuses.includes(s.status);
    const view: BundleProgressView = { status: 'QUEUED', done: 0, total: 0 };
    const emit = () => onProgress?.({ ...view });

    const shouldStop = (frame: BundleFrame): boolean => {
        if (frame.type === 'snapshot') return stopStatuses.includes(frame.state.status);
        if (frame.type === 'status') return stopStatuses.includes(frame.status);
        if (frame.type === 'completed') return stopStatuses.includes(frame.status);
        return frame.type === 'error' || frame.type === 'expired';
    };

    try {
        await streamBundleEvents(
            podId,
            eventsUrl,
            (frame) => {
                onFrame?.(frame);
                if (frame.type === 'snapshot') {
                    view.status = frame.state.status;
                    view.done = frame.state.progress?.done ?? view.done;
                    view.total = frame.state.progress?.total ?? view.total;
                } else if (frame.type === 'status') {
                    view.status = frame.status;
                } else if (frame.type === 'progress') {
                    view.done = frame.done;
                    view.total = frame.total;
                } else if (frame.type === 'completed') {
                    view.status = frame.status;
                }
                emit();
            },
            signal,
            shouldStop,
        );
        // Stream reached a stop frame (or closed) — read the authoritative state.
        return await poll(fetchStatus, isTerminal, { signal });
    } catch (error) {
        if ((error as Error)?.name === 'AbortError') throw error;
        // SSE unavailable — degrade to polling, still surfacing progress.
        return poll(fetchStatus, isTerminal, {
            signal,
            onTick: (s) => {
                view.status = s.status;
                view.done = s.progress?.done ?? 0;
                view.total = s.progress?.total ?? 0;
                emit();
            },
        });
    }
}

// ---------------------------------------------------------------------------
// Download
// ---------------------------------------------------------------------------

/**
 * Trigger a browser download of a signed bundle URL. The endpoint gates on the
 * logged-in user (cookie session), so an anchor navigation carries auth; the
 * server sets Content-Disposition to force the download.
 */
export function triggerBundleDownload(downloadUrl: string, filename?: string): void {
    const href = /^https?:\/\//i.test(downloadUrl)
        ? downloadUrl
        : `${getLemmaApiBaseUrl().replace(/\/$/, '')}${downloadUrl.startsWith('/') ? '' : '/'}${downloadUrl}`;
    const anchor = document.createElement('a');
    anchor.href = href;
    if (filename) anchor.download = filename;
    anchor.rel = 'noopener';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
}

// ---------------------------------------------------------------------------
// Small shared helpers
// ---------------------------------------------------------------------------

/** github.com/owner/repo (or owner/repo) → { owner, repo }. */
export function parseGithubRepo(input: string): { owner: string; repo: string } | null {
    const trimmed = input.trim();
    if (!trimmed) return null;
    const cleaned = trimmed
        .replace(/^https?:\/\/(www\.)?github\.com\//i, '')
        .replace(/\.git$/i, '')
        .replace(/\/$/, '');
    const parts = cleaned.split('/').filter(Boolean);
    if (parts.length < 2) return null;
    return { owner: parts[0], repo: parts[1] };
}

/** A human name → a safe-ish repo/slug (`My Pod!` → `my-pod`). */
export function toRepoSlug(value: string): string {
    return (value || '')
        .toLowerCase()
        .replace(/[^a-z0-9._-]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 90);
}
