import {
    ArrowDown,
    ArrowUp,
    Plus,
    Save,
    Trash2,
} from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { getAgentNodeName, getFunctionNodeName } from '@/lib/utils/flow-node-config';
import { FlowStart, NodeType } from '@/lib/types';

import { STEP_TYPE_ICONS, STEP_TYPE_LABELS } from './flow-editor-constants';
import { collectStepStats } from './flow-graph-ops';
import type { StepNode, StepType } from './flow-editor-types';

export function StepTypeBadge({ type }: { type: StepType }) {
    const Icon = STEP_TYPE_ICONS[type];
    return (
        <span className={cn(
            'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium',
            getStepBadgeClass(type)
        )}>
            <Icon className="h-3 w-3" />
            {STEP_TYPE_LABELS[type]}
        </span>
    );
}

export function StepOwnerBadge({ type }: { type: StepType }) {
    return (
        <span className={cn(
            'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
            getStepBadgeClass(type)
        )}>
            {getStepOwnerLabel(type)}
        </span>
    );
}

export function getStepOwnerLabel(type: StepType) {
    if (type === NodeType.FORM) return 'Human';
    if (type === NodeType.AGENT) return 'AI';
    if (type === NodeType.FUNCTION) return 'System';
    if (type === NodeType.DECISION) return 'Logic';
    if (type === NodeType.LOOP) return 'Loop';
    if (type === NodeType.WAIT_UNTIL) return 'Timer';
    return 'Done';
}

export function getStepBadgeClass(type: StepType) {
    if (type === NodeType.FORM) {
        return 'state-badge-info';
    }
    if (type === NodeType.AGENT) {
        return 'state-badge-success';
    }
    if (type === NodeType.FUNCTION) {
        return 'chip-muted';
    }
    if (type === NodeType.DECISION) {
        return 'state-badge-warning';
    }
    if (type === NodeType.LOOP) {
        return 'state-badge-brand';
    }
    if (type === NodeType.WAIT_UNTIL) {
        return 'state-badge-info';
    }
    return 'chip-muted';
}

export function getStepSurfaceClass(type: StepType, isSelected: boolean) {
    const selectedRing = isSelected
        ? 'tone-card-selected'
        : 'shadow-[var(--shadow-xs)] hover:shadow-[var(--shadow-sm)]';

    if (type === NodeType.AGENT) {
        return cn(
            'tone-card-success',
            selectedRing
        );
    }
    if (type === NodeType.FORM) {
        return cn(
            'tone-card-info',
            selectedRing
        );
    }
    if (type === NodeType.FUNCTION) {
        return cn(
            'tone-card-function',
            selectedRing
        );
    }
    if (type === NodeType.DECISION) {
        return cn(
            'tone-card-warning',
            selectedRing
        );
    }
    if (type === NodeType.LOOP) {
        return cn(
            'tone-card-brand',
            selectedRing
        );
    }
    if (type === NodeType.WAIT_UNTIL) {
        return cn(
            'tone-card-info',
            selectedRing
        );
    }
    return cn(
        'border-[color:color-mix(in_srgb,var(--text-secondary)_14%,var(--border-subtle))] bg-[color:color-mix(in_srgb,var(--card-bg)_82%,transparent)]',
        isSelected && 'border-[color:color-mix(in_srgb,var(--text-secondary)_30%,var(--card-border))]',
        selectedRing
    );
}

export function StepStatsPill({ stats }: { stats: ReturnType<typeof collectStepStats> }) {
    return (
        <div className="inline-flex items-center gap-2 rounded-full border border-[color:color-mix(in_srgb,var(--border-subtle)_58%,transparent)] bg-[color:color-mix(in_srgb,var(--card-bg)_58%,transparent)] px-3 py-1.5 text-xs text-[var(--text-tertiary)]">
            <span>{stats.total} total</span>
            <span className="h-1 w-1 rounded-full bg-[var(--card-border)]" />
            <span>{stats.configured} configured</span>
            <span className="h-1 w-1 rounded-full bg-[var(--card-border)]" />
            <span>{stats.branchCount} branches</span>
        </div>
    );
}

export function EditorActionFooter({
    stepStats,
    selectedStep,
    isStartSelected,
    isSaving,
    onAddStep,
    onSave,
    onMoveSelected,
    onDeleteSelected,
}: {
    stepStats: ReturnType<typeof collectStepStats>;
    selectedStep?: StepNode | null;
    isStartSelected?: boolean;
    isSaving?: boolean;
    onAddStep: (type: StepType) => void;
    onSave: () => void;
    onMoveSelected: (direction: 'up' | 'down') => void;
    onDeleteSelected: () => void;
}) {
    return (
        <footer className="shrink-0 border-t border-[color:color-mix(in_srgb,var(--border-subtle)_62%,transparent)] bg-[color:color-mix(in_srgb,var(--pod-main-bg)_88%,transparent)] px-5 py-3 shadow-[var(--shadow-sm)] backdrop-blur-md">
            <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-3">
                <div className="min-w-0">
                    <p className="type-eyebrow">Workflow draft</p>
                    <div className="mt-1">
                        <StepStatsPill stats={stepStats} />
                    </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                    {selectedStep && !isStartSelected ? (
                        <>
                            <Button type="button" variant="outline" size="icon" className="h-9 w-9" onClick={() => onMoveSelected('up')} title="Move step up" aria-label="Move step up">
                                <ArrowUp className="h-3.5 w-3.5" />
                            </Button>
                            <Button type="button" variant="outline" size="icon" className="h-9 w-9" onClick={() => onMoveSelected('down')} title="Move step down" aria-label="Move step down">
                                <ArrowDown className="h-3.5 w-3.5" />
                            </Button>
                            <Button type="button" variant="ghost" size="icon" className="h-9 w-9 text-[var(--state-error)]" onClick={onDeleteSelected} title="Delete step" aria-label="Delete step">
                                <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                        </>
                    ) : null}
                    <Button type="button" variant="outline" className="h-9 gap-2" onClick={() => onAddStep(NodeType.FORM)}>
                        <Plus className="h-3.5 w-3.5" />
                        Add step
                    </Button>
                    <Button type="button" className="h-9 gap-2 px-4" onClick={onSave} disabled={isSaving}>
                        <Save className="h-3.5 w-3.5" />
                        {isSaving ? 'Saving...' : 'Save'}
                    </Button>
                </div>
            </div>
        </footer>
    );
}

export function SectionTitle({ title }: { title: string }) {
    return <h4 className="font-display text-sm font-semibold text-[var(--text-secondary)]">{title}</h4>;
}

export function getStepSummary(step: StepNode, agentsById: Map<string, string>, functionsById: Map<string, string>): string {
    if (step.type === NodeType.AGENT) {
        const name = agentsById.get(getAgentNodeName(step.config));
        return name ? `Agent: ${name}` : 'Select an agent';
    }
    if (step.type === NodeType.FUNCTION) {
        const name = functionsById.get(getFunctionNodeName(step.config));
        return name ? `Function: ${name}` : 'Select a function';
    }
    if (step.type === NodeType.DECISION) {
        return `${step.branches?.length || 0} branches`;
    }
    if (step.type === NodeType.LOOP) {
        const path = String(step.config.items_path || '');
        return path ? `Items: ${path}` : 'Set items path';
    }
    if (step.type === NodeType.WAIT_UNTIL) {
        const timeout = Number(step.config.timeout_seconds || 0);
        return timeout > 0 ? `Wait ${timeout}s` : 'Set timeout';
    }
    if (step.type === NodeType.FORM) {
        return 'Collect user input';
    }
    return 'Finish workflow';
}

export function getStartSummary(flowStart?: FlowStart): string {
    const startType = flowStart?.type || 'MANUAL';
    const config = (flowStart?.config || {}) as Record<string, unknown>;

    if (startType === 'SCHEDULED') {
        return 'Runs from a Schedule';
    }

    if (startType === 'EVENT') {
        return String(config.connector_trigger_id || 'App event');
    }

    if (startType === 'DATASTORE_EVENT') {
        const table = String(config.table_name || config.name || 'Table change');
        const operations = Array.isArray(config.operations) && config.operations.length > 0
            ? ` · ${config.operations.join(', ')}`
            : '';
        return `${table}${operations}`;
    }

    return 'Run from the Runs page or API';
}

export function getConnectorLabel(connector: { id: string; title?: unknown; name?: unknown; description?: unknown }): string {
    return String(connector.title || connector.name || connector.description || connector.id);
}

export function getTriggerLabel(trigger: { id: string; name?: unknown; title?: unknown; event_type?: unknown; description?: unknown }): string {
    return String(trigger.name || trigger.title || trigger.event_type || trigger.description || trigger.id);
}

export function formatFieldType(type?: string) {
    if (!type) return 'Value';
    return type.charAt(0).toUpperCase() + type.slice(1);
}

export function getAssigneeLabel(
    step: StepNode,
    podMembers: Array<{
        pod_member_id?: string;
        user_id?: string;
        user_name?: string | null;
        user_email?: string | null;
        email?: string | null;
        role?: string;
    }>
): string {
    const expression = typeof step.config.assignee_pod_member_id_expression === 'string'
        ? step.config.assignee_pod_member_id_expression.trim()
        : '';
    if (expression) return expression;

    const assigneeId = typeof step.config.assignee_pod_member_id === 'string'
        ? step.config.assignee_pod_member_id
        : '';
    if (!assigneeId) return 'Anyone with access';

    const member = podMembers.find((candidate) => candidate.pod_member_id === assigneeId);
    return member?.user_name || member?.user_email || member?.email || assigneeId;
}

export function getNextStepSummary(step: StepNode, flattenedSteps: StepNode[]) {
    if (step.type === NodeType.DECISION) {
        const branches = step.branches || [];
        if (branches.length === 0) return 'No branches yet';
        return `${branches.length} branch${branches.length === 1 ? '' : 'es'}`;
    }

    if (step.type === NodeType.END) return 'Ends the run';

    const index = flattenedSteps.findIndex((candidate) => candidate.id === step.id);
    const nextStep = index >= 0 ? flattenedSteps[index + 1] : undefined;
    return nextStep ? nextStep.label : 'Ends the run';
}
