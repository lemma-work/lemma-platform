export function normalizeConversationStatus(status: unknown): string {
    return typeof status === 'string'
        ? status.trim().toLowerCase().replace(/[-_\s]+/g, '_')
        : '';
}

export type ConversationStatusState = 'running' | 'stopping' | 'waiting' | 'completed' | 'failed' | 'stopped' | 'unknown';
export type ConversationStatusTone = 'live' | 'warning' | 'neutral' | 'danger' | 'muted';

export interface ConversationStatusView {
    state: ConversationStatusState;
    label: string;
    dotLabel: string;
    tone: ConversationStatusTone;
    isActive: boolean;
    isAwaiting: boolean;
    isTerminal: boolean;
}

export function isConversationRunningStatus(status: unknown): boolean {
    return getConversationStatusView(status).state === 'running';
}

/**
 * How long a failure stays worth a colour in a list. `status` is sticky — it
 * keeps the shape of the last run forever — so colouring every past failure
 * turns a history into a wall of red that says nothing about now. A run that
 * failed while you were in the next room is worth catching; one that failed in
 * March is a fact about that conversation, not a thing to do.
 */
const RECENT_FAILURE_WINDOW_MS = 30 * 60 * 1000;

export type ConversationSignalTone = 'live' | 'warning' | 'danger' | 'none';

export interface ConversationSignal {
    tone: ConversationSignalTone;
    /** Whether the mark is solid. Hollow is the resting shape. */
    filled: boolean;
    /** Whether the mark should animate; only ever true for live work. */
    pulse: boolean;
    /** Null when the row carries no signal and should not be announced. */
    label: string | null;
}

const RESTING_SIGNAL: ConversationSignal = { tone: 'none', filled: false, pulse: false, label: null };

/**
 * What a conversation row should show at a glance, which is a narrower question
 * than what its status is. A list of thirty rows can only carry a signal if
 * almost all of them are quiet, so colour is spent on the two states that are
 * about right now — work in flight, and work stopped for you — plus failures
 * recent enough to still be news. Everything else rests.
 *
 * `now` is injected so the recency edge is testable rather than wall-clock.
 */
export function getConversationSignal(
    conversation: { status?: unknown; last_run_finished_at?: string | null },
    now: number = Date.now(),
): ConversationSignal {
    const view = getConversationStatusView(conversation.status);

    if (view.state === 'running') {
        return { tone: 'live', filled: true, pulse: true, label: view.label };
    }

    if (view.state === 'stopping' || view.state === 'waiting') {
        return { tone: 'warning', filled: true, pulse: false, label: view.label };
    }

    if (view.state === 'failed') {
        const finishedAt = conversation.last_run_finished_at
            ? new Date(conversation.last_run_finished_at).getTime()
            : Number.NaN;
        // An unparseable or absent timestamp means we cannot tell recent from
        // ancient, and a permanent red mark is the worse of the two mistakes.
        if (!Number.isFinite(finishedAt)) return RESTING_SIGNAL;
        if (now - finishedAt > RECENT_FAILURE_WINDOW_MS) return RESTING_SIGNAL;

        return { tone: 'danger', filled: true, pulse: false, label: view.label };
    }

    return RESTING_SIGNAL;
}

export function formatConversationStatus(status: unknown): string {
    return getConversationStatusView(status).label;
}

export function getConversationStatusView(status: unknown): ConversationStatusView {
    const normalized = normalizeConversationStatus(status);

    if (normalized === 'running' || normalized === 'in_progress' || normalized === 'processing') {
        return {
            state: 'running',
            label: 'Working',
            dotLabel: 'Live',
            tone: 'live',
            isActive: true,
            isAwaiting: false,
            isTerminal: false,
        };
    }

    if (normalized === 'stop_requested' || normalized === 'stopping') {
        return {
            state: 'stopping',
            label: 'Stopping',
            dotLabel: 'Stopping',
            tone: 'warning',
            isActive: true,
            isAwaiting: false,
            isTerminal: false,
        };
    }

    if (normalized === 'waiting' || normalized === 'awaiting' || normalized === 'waiting_for_input') {
        return {
            state: 'waiting',
            label: 'Awaiting input',
            dotLabel: 'Awaiting',
            tone: 'warning',
            isActive: false,
            isAwaiting: true,
            isTerminal: false,
        };
    }

    if (normalized === 'failed' || normalized === 'error') {
        return {
            state: 'failed',
            label: 'Failed',
            dotLabel: 'Failed',
            tone: 'danger',
            isActive: false,
            isAwaiting: false,
            isTerminal: true,
        };
    }

    if (normalized === 'stopped' || normalized === 'cancelled' || normalized === 'canceled') {
        return {
            state: 'stopped',
            label: 'Stopped',
            dotLabel: 'Stopped',
            tone: 'muted',
            isActive: false,
            isAwaiting: false,
            isTerminal: true,
        };
    }

    if (normalized === 'completed' || normalized === 'complete' || normalized === 'done') {
        return {
            state: 'completed',
            label: 'Done',
            dotLabel: 'Done',
            tone: 'neutral',
            isActive: false,
            isAwaiting: false,
            isTerminal: true,
        };
    }

    return {
        state: 'unknown',
        label: 'Ready',
        dotLabel: 'Ready',
        tone: 'muted',
        isActive: false,
        isAwaiting: false,
        isTerminal: false,
    };
}
