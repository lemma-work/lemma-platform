import {
    AlertCircle,
    Bot,
    Box,
    CheckCircle,
    Clock,
    Code2,
    Flag,
    GitBranch,
    Loader2,
    Play,
    Repeat,
    Timer,
    UserRound,
    XCircle,
} from '@/components/ui/icons';
import { NodeType, WorkflowNode, WorkflowRun } from '@/lib/types';
import { getPreviewFields, truncatePreview } from '@/lib/utils/payload-preview';

export type StatusVariant = 'default' | 'success' | 'error' | 'warning' | 'info';

export const toRunStatus = (status: unknown): string => (typeof status === 'string' ? status : 'UNKNOWN');

export const AGENT_CONVERSATION_POINTER_KEYS = new Set([
    'agent_conversation_id',
    'waiting_agent_conversation_id',
    'conversation_id',
    'external_conversation_id',
    'external_task_id',
    'assigned_pod_member_id',
    'node_id',
    'workflow_run_id',
    'run_id',
    'wait_type',
]);

export const ACTIVE_STEP_STATUSES = new Set(['RUNNING', 'PENDING', 'EXECUTING', 'IN_PROGRESS', 'PROCESSING']);
export const WAITING_STATUSES = new Set(['WAITING', 'WAITING_FOR_INPUT']);
export const COMPLETE_STATUSES = new Set(['COMPLETED', 'SUCCESS', 'SUCCEEDED']);
export const FAILURE_STATUSES = new Set(['FAILED', 'ERROR', 'CANCELLED', 'CANCELED']);

export type RunLike = {
    current_node_id?: string | null;
    step_history?: Array<Record<string, unknown>> | null;
};
export type StepSelectionMode = 'follow' | 'inspect';

export type RunCardRun = WorkflowRun | RunLike & {
    id?: string;
    status?: unknown;
    started_at?: string | null;
    created_at?: string | null;
    completed_at?: string | null;
    updated_at?: string | null;
};

export type WorkflowEdgeLike = {
    source?: string | null;
    target?: string | null;
};

export type RunTraceEntry = {
    key: string;
    nodeId: string;
    node: WorkflowNode | null;
    step: Record<string, unknown> | null;
    state: ProcedureStepState;
    label: string;
    occurrence: number;
    index: number;
};

export const UNREACHED_NODE_SELECTION_PREFIX = 'node:';

export function isRecord(value: unknown): value is Record<string, unknown> {
    return !!value && typeof value === 'object' && !Array.isArray(value);
}

export function hasVisibleData(value: unknown): boolean {
    if (value === null || typeof value === 'undefined') return false;
    if (typeof value === 'string') return value.trim().length > 0;
    if (Array.isArray(value)) return value.length > 0;
    if (isRecord(value)) return Object.keys(value).length > 0;
    return true;
}

export function isActiveStepStatus(status: string): boolean {
    return ACTIVE_STEP_STATUSES.has(status);
}

export function getAgentConversationId(outputData: unknown): string | null {
    if (!isRecord(outputData)) return null;
    const candidate = outputData.agent_conversation_id
        ?? outputData.waiting_agent_conversation_id
        ?? outputData.conversation_id
        ?? outputData.external_conversation_id
        ?? outputData.external_task_id;
    return typeof candidate === 'string' ? candidate : null;
}

export function getAgentDisplayOutput(outputData: unknown): unknown {
    const parsedOutput = parseStructuredPayload(outputData);
    if (!isRecord(parsedOutput)) return parsedOutput;

    return Object.fromEntries(
        Object.entries(parsedOutput).filter(([key]) => !AGENT_CONVERSATION_POINTER_KEYS.has(key))
    );
}

export function getVisibleStepData(value: unknown): unknown {
    const displayValue = getAgentDisplayOutput(value);
    if (!isRecord(displayValue)) return displayValue;

    const allEntries = Object.entries(displayValue);
    const entries = allEntries.filter(([, entryValue]) => hasVisibleData(entryValue));
    if (entries.length === 0) return allEntries.length > 0 ? displayValue : null;
    return Object.fromEntries(entries);
}

export function parseStructuredPayload(value: unknown): unknown {
    if (typeof value !== 'string') return value;
    const trimmed = value.trim();
    if (!trimmed || (!trimmed.startsWith('{') && !trimmed.startsWith('['))) return value;

    try {
        return JSON.parse(trimmed);
    } catch {
        return value;
    }
}

export function extractStructuredEntryText(entry: unknown): string {
    if (typeof entry === 'string') return entry.trim();
    if (!isRecord(entry)) return '';

    if (typeof entry.text === 'string') return entry.text.trim();
    if (typeof entry.content === 'string') return entry.content.trim();
    if (typeof entry.value === 'string') return entry.value.trim();

    if (Array.isArray(entry.content)) {
        return entry.content
            .map((child) => extractStructuredEntryText(child))
            .filter(Boolean)
            .join('\n')
            .trim();
    }

    if (Array.isArray(entry.summary)) {
        return entry.summary
            .map((child) => extractStructuredEntryText(child))
            .filter(Boolean)
            .join('\n')
            .trim();
    }

    return '';
}

export function extractConversationContentText(content: unknown): string {
    if (typeof content === 'string') return content.trim();

    if (Array.isArray(content)) {
        return content
            .map((entry) => extractStructuredEntryText(entry))
            .filter(Boolean)
            .join('\n\n')
            .trim();
    }

    if (!isRecord(content)) return '';

    if (typeof content.content === 'string') return content.content.trim();
    if (Array.isArray(content.content)) {
        const nested = content.content
            .map((entry) => extractStructuredEntryText(entry))
            .filter(Boolean)
            .join('\n\n')
            .trim();
        if (nested) return nested;
    }

    if (typeof content.text === 'string') return content.text.trim();
    if (typeof content.value === 'string') return content.value.trim();

    return extractStructuredEntryText(content);
}

export function formatStructuredOutput(value: unknown): string {
    if (typeof value === 'string') return value;
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

export function getFinalStructuredOutput(messages: Array<{ metadata?: Record<string, unknown> | null }>): unknown | undefined {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
        const metadata = messages[index]?.metadata;
        if (isRecord(metadata) && metadata.is_final_answer === true && 'structured_output' in metadata) {
            return metadata.structured_output;
        }
    }

    return undefined;
}

export function parseApiDate(value: unknown): Date | null {
    if (typeof value !== 'string' || !value.trim()) return null;

    const normalized = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
    const parsed = new Date(normalized);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function getRunDisplayDate(run: {
    started_at?: string | null;
    created_at?: string | null;
}): Date | null {
    return parseApiDate(run.started_at) || parseApiDate(run.created_at);
}

export function getRunSortTime(run: {
    started_at?: string | null;
    created_at?: string | null;
    completed_at?: string | null;
    updated_at?: string | null;
    id?: string | null;
}): number {
    const displayDate = getRunDisplayDate(run);
    if (displayDate) return displayDate.getTime();

    const fallbackDate = parseApiDate(run.completed_at) || parseApiDate(run.updated_at);
    if (fallbackDate) return fallbackDate.getTime();

    const uuidV7Timestamp = typeof run.id === 'string' ? Number.parseInt(run.id.slice(0, 12), 16) : Number.NaN;
    return Number.isFinite(uuidV7Timestamp) ? uuidV7Timestamp : 0;
}

export function formatDuration(startedAt: Date | null, completedAt: Date | null): string | null {
    if (!startedAt || !completedAt) return null;

    const diffMs = completedAt.getTime() - startedAt.getTime();
    if (!Number.isFinite(diffMs) || diffMs < 0) return null;

    const totalSeconds = Math.round(diffMs / 1000);
    if (totalSeconds < 60) return `${totalSeconds}s`;

    const totalMinutes = Math.round(totalSeconds / 60);
    if (totalMinutes < 60) return `${totalMinutes}m`;

    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
}

export function formatPreciseDuration(startedAt: unknown, completedAt: unknown): string | null {
    const started = parseApiDate(startedAt);
    const completed = parseApiDate(completedAt);
    if (!started || !completed) return null;

    const diffMs = completed.getTime() - started.getTime();
    if (!Number.isFinite(diffMs) || diffMs < 0) return null;

    if (diffMs < 1000) return `${diffMs}ms`;
    const seconds = diffMs / 1000;
    if (seconds < 10) return `${seconds.toFixed(1)}s`;
    if (seconds < 60) return `${Math.round(seconds)}s`;

    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return `${minutes}m`;

    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;
    return remainingMinutes ? `${hours}h ${remainingMinutes}m` : `${hours}h`;
}

export function formatTimestamp(date: Date | null): string {
    if (!date) return 'Unknown';
    return new Intl.DateTimeFormat(undefined, {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
    }).format(date);
}

export function getLaterDate(a: Date | null, b: Date | null): Date | null {
    if (!a) return b;
    if (!b) return a;
    return a.getTime() >= b.getTime() ? a : b;
}

export function getRunLastActivityDate(run: {
    completed_at?: string | null;
    step_history?: Array<Record<string, unknown>> | null;
    started_at?: string | null;
    created_at?: string | null;
} | null | undefined): Date | null {
    if (!run) return null;

    let latest = parseApiDate(run.completed_at) || null;

    for (const step of run.step_history || []) {
        latest = getLaterDate(latest, parseApiDate(step.completed_at));
        latest = getLaterDate(latest, parseApiDate(step.started_at));
    }

    return latest || getRunDisplayDate(run);
}

export function getRunFreshnessTime(run: RunCardRun | null | undefined): number {
    if (!run) return 0;

    const lastActivityAt = getRunLastActivityDate(run);
    if (lastActivityAt) return lastActivityAt.getTime();

    const fallback = parseApiDate(run.updated_at) || parseApiDate(run.completed_at) || parseApiDate(run.started_at) || parseApiDate(run.created_at);
    return fallback?.getTime() ?? 0;
}

export function pickFreshestRun(primary: RunCardRun | null | undefined, secondary: RunCardRun | null | undefined): RunCardRun | null {
    if (!primary) return secondary ?? null;
    if (!secondary) return primary ?? null;
    if (primary.id && secondary.id && primary.id !== secondary.id) return primary;
    return getRunFreshnessTime(secondary) > getRunFreshnessTime(primary) ? secondary : primary;
}

export function formatStatusLabel(status: string): string {
    return status.toLowerCase().replace(/_/g, ' ');
}

export function getStatusVariant(status: string): StatusVariant {
    switch (status) {
        case 'COMPLETED':
            return 'success';
        case 'FAILED':
            return 'error';
        case 'WAITING':
        case 'WAITING_FOR_INPUT':
            return 'warning';
        case 'RUNNING':
        case 'PENDING':
        case 'EXECUTING':
            return 'info';
        default:
            return 'default';
    }
}

export function getStatusIcon(status: string) {
    switch (status) {
        case 'COMPLETED':
            return <CheckCircle className="h-4 w-4" />;
        case 'FAILED':
            return <XCircle className="h-4 w-4" />;
        case 'WAITING':
        case 'WAITING_FOR_INPUT':
            return <Clock className="h-4 w-4" />;
        case 'RUNNING':
        case 'PENDING':
        case 'EXECUTING':
            return <Loader2 className="h-4 w-4 animate-spin" />;
        default:
            return <AlertCircle className="h-4 w-4" />;
    }
}

export function formatRunIdShort(runId: string | null | undefined): string {
    if (!runId) return 'unknown';
    return runId.slice(0, 8);
}

export function getNodeLabel(nodeId: string | null | undefined, nodes: WorkflowNode[]): string | null {
    if (!nodeId) return null;
    const node = nodes.find((entry) => entry.id === nodeId);
    return node ? getDisplayNodeLabel(node) : nodeId;
}

export function getNodeTypeLabel(type: string | undefined): string {
    if (type === NodeType.FORM) return 'Input';
    if (type === NodeType.AGENT) return 'Agent';
    if (type === NodeType.FUNCTION) return 'Function';
    if (type === NodeType.WAIT_UNTIL) return 'Wait';
    if (type === NodeType.END) return 'Done';
    if (type === NodeType.DECISION) return 'Decision';
    if (type === NodeType.LOOP) return 'Loop';
    return type || 'Step';
}

export function getNodeAgentName(node: WorkflowNode | null | undefined): string | null {
    if (!node || node.type !== NodeType.AGENT || !isRecord(node.config)) return null;
    const candidate = node.config.agent_name
        ?? node.config.agentName
        ?? node.config.agent
        ?? node.config.assistant_name
        ?? node.config.assistantName;
    return typeof candidate === 'string' && candidate.trim() ? candidate.trim() : null;
}

export function getRunProgress(run: RunCardRun, nodes: WorkflowNode[], currentNodeId: string | null, runStatus: string): number {
    if (nodes.length === 0) return 0;

    const latestStepByNode = getLatestStepByNode(run.step_history || []);
    const currentNodeIndex = getNodeIndex(currentNodeId, nodes);
    const activeIndex = currentNodeIndex >= 0
        ? currentNodeIndex
        : COMPLETE_STATUSES.has(runStatus)
            ? Math.max(0, nodes.length - 1)
            : 0;
    const completedSteps = nodes.filter((node, index) => {
        const step = latestStepByNode.get(node.id) || null;
        const state = getProcedureStepState({
            node,
            step,
            index,
            currentNodeId: currentNodeId ?? undefined,
            currentNodeIndex,
            runStatus,
        });
        return state === 'completed';
    }).length;

    return Math.round(((COMPLETE_STATUSES.has(runStatus) ? nodes.length : Math.max(completedSteps, activeIndex)) / nodes.length) * 100);
}

export function getDisplayNodeLabel(node: WorkflowNode | null | undefined): string {
    if (!node) return 'Step';
    if (node.type !== NodeType.FORM) return node.label || getNodeTypeLabel(node.type);

    const label = (node.label || '').trim();
    if (!label) return 'Input request';
    const withoutForm = label.replace(/\bform\b/gi, 'input').replace(/\s+/g, ' ').trim();
    if (/^input input$/i.test(withoutForm) || /^input$/i.test(withoutForm)) return 'Input request';
    return withoutForm;
}

export function getNodeIndex(nodeId: string | null | undefined, nodes: WorkflowNode[]): number {
    if (!nodeId) return -1;
    return nodes.findIndex((entry) => entry.id === nodeId);
}

export function getStepPositionLabel(nodeId: string | null | undefined, nodes: WorkflowNode[]): string {
    const index = getNodeIndex(nodeId, nodes);
    return index >= 0 ? `Step ${index + 1} of ${nodes.length}` : `${nodes.length} step${nodes.length === 1 ? '' : 's'}`;
}

export function getNextNode(nodeId: string | null | undefined, nodes: WorkflowNode[]): WorkflowNode | null {
    const index = getNodeIndex(nodeId, nodes);
    if (index < 0) return null;
    return nodes[index + 1] || null;
}

export function getLastStepEntry(run: RunLike): Record<string, unknown> | null {
    const history = run.step_history || [];
    return (history[history.length - 1] as Record<string, unknown> | undefined) || null;
}

export function getRunCurrentNodeId(run: RunLike): string | null {
    const fallback = getLastStepEntry(run)?.node_id;
    return run.current_node_id || (typeof fallback === 'string' ? fallback : null);
}

export function getRunHistoryTitle(status: string): string {
    if (WAITING_STATUSES.has(status)) return 'Waiting for input';
    if (status === 'CANCELLED' || status === 'CANCELED') return 'Cancelled';
    if (FAILURE_STATUSES.has(status)) return 'Failed';
    if (COMPLETE_STATUSES.has(status)) return 'Completed';
    if (isActiveStepStatus(status)) return 'Running';
    return 'Run created';
}

export function getRunHistoryDetail(run: WorkflowRun, nodes: WorkflowNode[]): string {
    const status = toRunStatus(run.status);
    const currentNodeId = getRunCurrentNodeId(run);
    const currentNodeLabel = getNodeLabel(currentNodeId, nodes);
    const stepPosition = getStepPositionLabel(currentNodeId, nodes);
    const nextNode = getNextNode(currentNodeId, nodes);
    const duration = formatDuration(getRunDisplayDate(run), parseApiDate(run.completed_at));

    if (WAITING_STATUSES.has(status)) {
        return `${currentNodeLabel || 'A step'} · ${stepPosition}${nextNode ? ` · Next: ${getDisplayNodeLabel(nextNode)}` : ''}`;
    }

    if (FAILURE_STATUSES.has(status)) {
        return `${currentNodeLabel || 'A step'} · ${stepPosition}`;
    }

    if (COMPLETE_STATUSES.has(status)) {
        return `${run.step_history?.length || nodes.length} of ${nodes.length} steps${duration ? ` · ${duration}` : ''}`;
    }

    if (isActiveStepStatus(status)) {
        return `${currentNodeLabel || 'Starting'} · ${stepPosition}`;
    }

    return `${nodes.length} step${nodes.length === 1 ? '' : 's'}`;
}

export function getRunHistorySubdetail(run: WorkflowRun, nodes: WorkflowNode[]): string {
    const currentNodeId = getRunCurrentNodeId(run);
    const nextNode = getNextNode(currentNodeId, nodes);
    const status = toRunStatus(run.status);

    if (WAITING_STATUSES.has(status)) {
        return nextNode ? `Continue to ${getDisplayNodeLabel(nextNode)}` : 'Ready to continue';
    }

    if (COMPLETE_STATUSES.has(status)) {
        return nodes.map((node) => getDisplayNodeLabel(node)).slice(0, 4).join(' -> ');
    }

    if (FAILURE_STATUSES.has(status)) {
        return 'Open the run to inspect the failed step';
    }

    if (isActiveStepStatus(status)) {
        return nextNode ? `Heading toward ${getDisplayNodeLabel(nextNode)}` : 'Moving through the procedure';
    }

    return 'No step activity recorded yet';
}

export function getStepSummaryText(value: unknown): string | null {
    if (typeof value === 'string' && value.trim()) {
        return truncatePreview(value, 140);
    }

    if (isRecord(value)) {
        const previewFields = getPreviewFields(value);
        if (previewFields.length > 0) {
            return truncatePreview(`${previewFields[0].label}: ${previewFields[0].value}`, 140);
        }
    }

    return null;
}

export function getSelectedPlaybackNodeId(
    selection: { nodeId: string; mode: StepSelectionMode } | null | undefined,
    nodes: WorkflowNode[],
    currentNodeId: string | null
): string | null {
    if (!selection) return null;
    if (selection.mode === 'inspect') return selection.nodeId;

    const selectedNodeIndex = getNodeIndex(selection.nodeId, nodes);
    const currentNodeIndex = getNodeIndex(currentNodeId, nodes);
    if (selectedNodeIndex < 0) return null;
    if (currentNodeIndex < 0) return selection.nodeId;

    return selectedNodeIndex > currentNodeIndex ? selection.nodeId : null;
}

export function getStepSelectionMode({
    nodeId,
    run,
    nodes,
    currentNodeId,
    runStatus,
}: {
    nodeId: string;
    run: RunCardRun | null;
    nodes: WorkflowNode[];
    currentNodeId: string | null;
    runStatus: string;
}): StepSelectionMode {
    if (!run) return 'follow';

    const selectedNodeIndex = getNodeIndex(nodeId, nodes);
    const currentNodeIndex = getNodeIndex(currentNodeId, nodes);
    if (selectedNodeIndex < 0) return 'follow';
    if (COMPLETE_STATUSES.has(runStatus)) return 'inspect';
    if (currentNodeIndex >= 0 && selectedNodeIndex >= currentNodeIndex) return 'follow';

    const node = nodes[selectedNodeIndex];
    const latestStepByNode = getLatestStepByNode(run.step_history || []);
    const state = getProcedureStepState({
        node,
        step: latestStepByNode.get(node.id) || null,
        index: selectedNodeIndex,
        currentNodeId: currentNodeId ?? undefined,
        currentNodeIndex,
        runStatus,
    });

    return state === 'completed' || state === 'failed' ? 'inspect' : 'follow';
}

export type ProcedureStepState = 'completed' | 'waiting' | 'running' | 'failed' | 'next' | 'pending';
export type RunCompletionTiming = { startedAt: Date | null; completedAt: Date | null; duration: string | null };

export function isReachedStepState(state: ProcedureStepState): boolean {
    return state === 'completed' || state === 'waiting' || state === 'running' || state === 'failed';
}

export function getProcedureStepState({
    node,
    step,
    index,
    currentNodeId,
    currentNodeIndex,
    runStatus,
}: {
    node: WorkflowNode;
    step: Record<string, unknown> | null;
    index: number;
    currentNodeId?: string;
    currentNodeIndex: number;
    runStatus: string;
}): ProcedureStepState {
    const stepStatus = toRunStatus(step?.status);
    const isCurrent = currentNodeId === node.id;

    if (isCurrent && WAITING_STATUSES.has(runStatus)) return 'waiting';
    if (isCurrent && FAILURE_STATUSES.has(runStatus)) return 'failed';
    if (isCurrent && isActiveStepStatus(runStatus)) return 'running';
    if (currentNodeIndex >= 0 && index > currentNodeIndex && !COMPLETE_STATUSES.has(runStatus)) {
        return index === currentNodeIndex + 1 ? 'next' : 'pending';
    }
    if (FAILURE_STATUSES.has(stepStatus)) return 'failed';
    if (COMPLETE_STATUSES.has(stepStatus)) return 'completed';
    if (step && hasVisibleData(step.output_data)) return 'completed';
    if (COMPLETE_STATUSES.has(runStatus) && step) return 'completed';
    return 'pending';
}

export function getProcedureStepStatusLabel(state: ProcedureStepState): string {
    if (state === 'completed') return 'Done';
    if (state === 'waiting') return 'Waiting';
    if (state === 'running') return 'Running';
    if (state === 'failed') return 'Failed';
    if (state === 'next') return 'Next';
    return 'Pending';
}

export function getProcedureStatusForVariant(state: ProcedureStepState): string {
    if (state === 'completed') return 'COMPLETED';
    if (state === 'waiting') return 'WAITING';
    if (state === 'running') return 'RUNNING';
    if (state === 'failed') return 'FAILED';
    return 'PENDING';
}

export function getStepNarrative({
    node,
    state,
    nextNodeLabel,
    index,
    totalSteps,
}: {
    node: WorkflowNode;
    state: ProcedureStepState;
    nextNodeLabel: string | null;
    index: number;
    totalSteps: number;
}): string {
    const label = getDisplayNodeLabel(node);

    if (state === 'waiting') {
        return nextNodeLabel ? `Waiting here before continuing to ${nextNodeLabel}.` : 'Waiting for the required input.';
    }

    if (state === 'running') {
        return nextNodeLabel ? `Working now. Next up: ${nextNodeLabel}.` : 'Working through the last step.';
    }

    if (state === 'completed') {
        return nextNodeLabel ? `Completed and handed off to ${nextNodeLabel}.` : 'Completed the workflow path.';
    }

    if (state === 'failed') {
        return `${label} stopped the run. Review the step output before retrying.`;
    }

    if (state === 'next') {
        return `Runs after step ${index} completes.`;
    }

    return index + 1 === totalSteps ? 'Waiting for the workflow to reach the final step.' : 'Not reached in this run yet.';
}

export function getNodeIconElement(type: string | undefined) {
    switch (type) {
        case NodeType.START:
            return <Play className="h-4 w-4" />;
        case NodeType.AGENT:
            return <Bot className="h-4 w-4" />;
        case NodeType.FUNCTION:
            return <Code2 className="h-4 w-4" />;
        case NodeType.FORM:
            return <UserRound className="h-4 w-4" />;
        case NodeType.DECISION:
            return <GitBranch className="h-4 w-4" />;
        case NodeType.LOOP:
            return <Repeat className="h-4 w-4" />;
        case NodeType.WAIT_UNTIL:
            return <Timer className="h-4 w-4" />;
        case NodeType.END:
            return <Flag className="h-4 w-4" />;
        default:
            return <Box className="h-4 w-4" />;
    }
}

export function getRunTraceEntries(
    run: RunCardRun,
    nodes: WorkflowNode[],
    currentNodeId: string | null,
    runStatus: string
): RunTraceEntry[] {
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const occurrenceByNodeId = new Map<string, number>();
    const entries: RunTraceEntry[] = [];

    for (const rawStep of run.step_history || []) {
        if (!isRecord(rawStep) || typeof rawStep.node_id !== 'string') continue;

        const nodeId = rawStep.node_id;
        const occurrence = (occurrenceByNodeId.get(nodeId) || 0) + 1;
        occurrenceByNodeId.set(nodeId, occurrence);
        const node = nodeById.get(nodeId) || null;

        entries.push({
            key: `${nodeId}:${entries.length}:${occurrence}`,
            nodeId,
            node,
            step: rawStep,
            state: getTraceStepState(rawStep, nodeId, currentNodeId, runStatus),
            label: node ? getDisplayNodeLabel(node) : nodeId,
            occurrence,
            index: entries.length,
        });
    }

    if (entries.length === 0 && currentNodeId) {
        const node = nodeById.get(currentNodeId) || null;
        entries.push({
            key: `${currentNodeId}:current`,
            nodeId: currentNodeId,
            node,
            step: null,
            state: WAITING_STATUSES.has(runStatus)
                ? 'waiting'
                : FAILURE_STATUSES.has(runStatus)
                    ? 'failed'
                    : isActiveStepStatus(runStatus)
                        ? 'running'
                        : 'pending',
            label: node ? getDisplayNodeLabel(node) : currentNodeId,
            occurrence: 1,
            index: 0,
        });
    }

    return entries;
}

export function getTraceStepState(
    step: Record<string, unknown> | null,
    nodeId: string,
    currentNodeId: string | null,
    runStatus: string
): ProcedureStepState {
    const stepStatus = toRunStatus(step?.status);

    if (nodeId === currentNodeId && WAITING_STATUSES.has(runStatus)) return 'waiting';
    if (nodeId === currentNodeId && FAILURE_STATUSES.has(runStatus)) return 'failed';
    if (nodeId === currentNodeId && isActiveStepStatus(runStatus)) return 'running';
    if (WAITING_STATUSES.has(stepStatus)) return 'waiting';
    if (FAILURE_STATUSES.has(stepStatus)) return 'failed';
    if (isActiveStepStatus(stepStatus)) return 'running';
    if (COMPLETE_STATUSES.has(stepStatus) || hasVisibleData(step?.output_data)) return 'completed';
    return 'pending';
}

export function getNodeOutgoingCount(nodeId: string, edges: WorkflowEdgeLike[]): number {
    return edges.filter((edge) => edge.source === nodeId).length;
}

export function getNextGraphNode(nodeId: string | null | undefined, nodes: WorkflowNode[], edges: WorkflowEdgeLike[]): WorkflowNode | null {
    if (!nodeId) return null;
    const firstTargetId = edges.find((edge) => edge.source === nodeId)?.target;
    if (!firstTargetId) return null;
    return nodes.find((node) => node.id === firstTargetId) || null;
}

export function getGraphShapeLabel(nodes: WorkflowNode[], edges: WorkflowEdgeLike[]): string {
    const branchCount = nodes.filter((node) => getNodeOutgoingCount(node.id, edges) > 1).length;
    const loopCount = edges.filter((edge) => {
        const sourceIndex = nodes.findIndex((node) => node.id === edge.source);
        const targetIndex = nodes.findIndex((node) => node.id === edge.target);
        return sourceIndex >= 0 && targetIndex >= 0 && targetIndex <= sourceIndex;
    }).length;
    const parts = [`${nodes.length} node${nodes.length === 1 ? '' : 's'}`];

    if (branchCount > 0) parts.push(`${branchCount} branch${branchCount === 1 ? '' : 'es'}`);
    if (loopCount > 0) parts.push(`${loopCount} loop${loopCount === 1 ? '' : 's'}`);
    if (parts.length === 1 && edges.length > 0) parts.push(`${edges.length} connection${edges.length === 1 ? '' : 's'}`);

    return parts.join(' · ');
}

export function getLatestStepByNode(stepHistory: unknown[]): Map<string, Record<string, unknown>> {
    const map = new Map<string, Record<string, unknown>>();
    for (const step of stepHistory) {
        if (!isRecord(step) || typeof step.node_id !== 'string') continue;
        map.set(step.node_id, step);
    }
    return map;
}

// Top-level property defaults from a resolved schema, used to prefill the form.
export function formDefaults(schema: Record<string, unknown> | undefined): Record<string, unknown> {
    if (!isRecord(schema)) return {};
    const properties = isRecord(schema.properties) ? schema.properties : {};
    const data: Record<string, unknown> = {};
    for (const [key, prop] of Object.entries(properties)) {
        if (isRecord(prop) && 'default' in prop && prop.default !== undefined && prop.default !== null) {
            data[key] = prop.default;
        }
    }
    return data;
}
