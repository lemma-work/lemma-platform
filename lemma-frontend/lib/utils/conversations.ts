export function normalizeConversationStatus(status: unknown): string {
    return typeof status === 'string'
        ? status.trim().toLowerCase().replace(/[-_\s]+/g, '_')
        : '';
}

function appendAgentScope(href: string, agentName?: string | null): string {
    const normalizedAgentName = agentName?.trim();
    if (!normalizedAgentName) return href;

    const params = new URLSearchParams({ agent: normalizedAgentName });
    return `${href}?${params.toString()}`;
}

export function buildPodConversationsHref(podId: string, agentName?: string | null): string {
    return appendAgentScope(`/pod/${encodeURIComponent(podId)}/conversations`, agentName);
}

export function buildPodConversationHref(
    podId: string,
    conversationId: string,
    agentName?: string | null,
): string {
    const href = `/pod/${encodeURIComponent(podId)}/conversations/${encodeURIComponent(conversationId)}`;
    return appendAgentScope(href, agentName);
}

export function getConversationRouteId(pathname: string): string | null {
    const match = /^\/pod\/[^/]+\/conversations\/([^/]+)\/?$/.exec(pathname);
    if (!match?.[1]) return null;

    try {
        const conversationId = decodeURIComponent(match[1]);
        return conversationId === 'new' ? null : conversationId;
    } catch {
        return match[1] === 'new' ? null : match[1];
    }
}

export function findConversationAgentName(
    agentId: string | null | undefined,
    agents: Array<{ id?: string | null; name?: string | null }> | null | undefined,
): string | null {
    if (!agentId || !agents) return null;
    return agents.find((agent) => agent.id === agentId)?.name?.trim() || null;
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
