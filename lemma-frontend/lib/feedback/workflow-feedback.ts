import type { SoundFeedbackEvent } from './sound-feedback';

export type WorkflowRunFeedbackState =
    | 'active'
    | 'waiting'
    | 'complete'
    | 'failed'
    | 'cancelled'
    | 'unknown';

export type WorkflowRunFeedbackSnapshot = {
    id: string;
    state: WorkflowRunFeedbackState;
    timestamp: number;
};

type WorkflowRunLike = {
    id?: unknown;
    status?: unknown;
    created_at?: unknown;
    started_at?: unknown;
    updated_at?: unknown;
    completed_at?: unknown;
};

const ACTIVE_STATUSES = new Set(['PENDING', 'RUNNING', 'EXECUTING', 'IN_PROGRESS', 'PROCESSING']);
const WAITING_STATUSES = new Set(['WAITING', 'WAITING_FOR_INPUT']);
const COMPLETE_STATUSES = new Set(['COMPLETED', 'SUCCESS', 'SUCCEEDED']);
const FAILED_STATUSES = new Set(['FAILED', 'ERROR']);
const CANCELLED_STATUSES = new Set(['CANCELLED', 'CANCELED']);

export function getWorkflowRunFeedbackState(status: unknown): WorkflowRunFeedbackState {
    const normalized = String(status || '').trim().toUpperCase();
    if (ACTIVE_STATUSES.has(normalized)) return 'active';
    if (WAITING_STATUSES.has(normalized)) return 'waiting';
    if (COMPLETE_STATUSES.has(normalized)) return 'complete';
    if (FAILED_STATUSES.has(normalized)) return 'failed';
    if (CANCELLED_STATUSES.has(normalized)) return 'cancelled';
    return 'unknown';
}

export function getWorkflowFeedbackEvent(
    previous: WorkflowRunFeedbackState | null,
    next: WorkflowRunFeedbackState,
): SoundFeedbackEvent | null {
    if (previous === next) return null;
    if (next === 'active') return previous ? 'work-start' : null;
    if (next === 'waiting') return previous ? 'work-waiting' : null;
    if (next === 'complete') {
        return previous === 'active' || previous === 'waiting' ? 'work-complete' : null;
    }
    if (next === 'failed') {
        return previous === 'active' || previous === 'waiting' ? 'work-fail' : null;
    }
    return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function isWorkflowRunLike(value: unknown): value is WorkflowRunLike {
    return isRecord(value) && typeof value.id === 'string' && 'status' in value;
}

function readRunTimestamp(run: WorkflowRunLike): number {
    for (const value of [run.updated_at, run.completed_at, run.started_at, run.created_at]) {
        if (typeof value !== 'string') continue;
        const timestamp = Date.parse(value);
        if (Number.isFinite(timestamp)) return timestamp;
    }
    return 0;
}

function collectRun(value: unknown, runs: WorkflowRunLike[]) {
    if (isWorkflowRunLike(value)) runs.push(value);
}

export function extractWorkflowRunFeedbackSnapshots(
    queryKey: readonly unknown[],
    data: unknown,
): WorkflowRunFeedbackSnapshot[] {
    const rootKey = queryKey[0];
    const runs: WorkflowRunLike[] = [];

    if (rootKey === 'flow-runs') {
        if (Array.isArray(data)) {
            data.forEach((value) => collectRun(value, runs));
        } else if (isWorkflowRunLike(data)) {
            runs.push(data);
        } else if (isRecord(data) && Array.isArray(data.pages)) {
            data.pages.forEach((page) => {
                if (!isRecord(page) || !Array.isArray(page.items)) return;
                page.items.forEach((value) => collectRun(value, runs));
            });
        }
    } else if (rootKey === 'workflow-run-snapshots' && Array.isArray(data)) {
        data.forEach((snapshot) => {
            if (!isRecord(snapshot) || !Array.isArray(snapshot.runs)) return;
            snapshot.runs.forEach((value) => collectRun(value, runs));
        });
    } else if (rootKey === 'workflow-run-waits' && isRecord(data) && Array.isArray(data.items)) {
        data.items.forEach((assignment) => {
            if (isRecord(assignment)) collectRun(assignment.run, runs);
        });
    }

    const snapshots = new Map<string, WorkflowRunFeedbackSnapshot>();
    for (const run of runs) {
        const id = String(run.id || '');
        if (!id) continue;
        const next = {
            id,
            state: getWorkflowRunFeedbackState(run.status),
            timestamp: readRunTimestamp(run),
        };
        const existing = snapshots.get(id);
        if (!existing || next.timestamp >= existing.timestamp) snapshots.set(id, next);
    }

    return [...snapshots.values()];
}
