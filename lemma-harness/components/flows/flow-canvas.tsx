import { memo } from 'react';
import { Play, Plus } from '@/components/ui/icons';
import {
    Handle,
    Position,
    type Edge as ReactFlowEdge,
    type Node as ReactFlowNode,
    type NodeProps,
} from '@xyflow/react';

import { cn } from '@/lib/utils';
import { FlowStart, FlowStartType, NodeType } from '@/lib/types';

import { START_NODE_ID, START_TYPE_ICONS, START_TYPE_LABELS, STEP_TYPE_ICONS, STEP_TYPE_LABELS } from './flow-editor-constants';
import { getStartSummary, getStepBadgeClass, getStepSummary, getStepSurfaceClass } from './flow-step-badges';
import type { CanvasPosition, StepNode, StepType } from './flow-editor-types';

type CanvasStartNodeData = {
    kind: 'start';
    startType: FlowStartType;
    title: string;
    summary: string;
    isSelected: boolean;
    onSelect: () => void;
} & Record<string, unknown>;

type CanvasNodeData = {
    kind: 'step';
    step: StepNode;
    summary: string;
    isSelected: boolean;
    onSelect: (stepId: string) => void;
} & Record<string, unknown>;

type CanvasActionNodeData = {
    kind: 'action';
    title: string;
    detail: string;
    actionLabel: string;
    onAction: () => void;
} & Record<string, unknown>;

export type CanvasGraph = {
    nodes: CanvasReactFlowNode[];
    edges: ReactFlowEdge[];
};

export type CanvasReactFlowNode =
    | ReactFlowNode<CanvasStartNodeData>
    | ReactFlowNode<CanvasNodeData>
    | ReactFlowNode<CanvasActionNodeData>;

const WorkflowStartNode = memo(function WorkflowStartNode({ data }: NodeProps<ReactFlowNode<CanvasStartNodeData>>) {
    const Icon = START_TYPE_ICONS[data.startType] || Play;

    return (
        <button
            type="button"
            onClick={data.onSelect}
            className={cn(
                'flow-canvas-node-button surface-panel tone-card-action relative flex w-[270px] items-center gap-4 px-5 py-5 transition-gentle',
                data.isSelected
                    ? 'tone-card-selected'
                    : 'hover:border-[var(--tone-card-selected-border)] hover:shadow-[var(--shadow-sm)]'
            )}
        >
            <span className="absolute left-5 top-3 type-eyebrow-medium">
                Start
            </span>
            <span className="tone-action-icon flex h-14 w-14 shrink-0 items-center justify-center rounded-lg">
                <Icon className="h-6 w-6 stroke-[2.3]" />
            </span>
            <span className="min-w-0 flex-1 pt-3">
                <span className="block truncate text-base font-semibold text-[var(--text-primary)]">{data.title}</span>
                <span className="mt-1 block truncate text-sm text-[var(--text-secondary)]">{data.summary}</span>
            </span>
            <Handle type="source" position={Position.Bottom} className="!h-2.5 !w-2.5 !border-2 !border-[var(--bg-canvas)] !bg-[var(--action-primary)]" />
        </button>
    );
});

WorkflowStartNode.displayName = 'WorkflowStartNode';

const WorkflowCanvasNode = memo(function WorkflowCanvasNode({ data }: NodeProps<ReactFlowNode<CanvasNodeData>>) {
    const step = data.step;
    const Icon = STEP_TYPE_ICONS[step.type];

    return (
        <button
            type="button"
            onClick={() => data.onSelect(step.id)}
            className={cn(
                'flow-canvas-node-button group relative flex w-[270px] items-center gap-4 rounded-lg border px-5 py-5 transition-gentle',
                getStepSurfaceClass(step.type, data.isSelected)
            )}
        >
            <Handle type="target" position={Position.Top} className="!h-2.5 !w-2.5 !border-2 !border-[var(--bg-canvas)] !bg-[var(--text-tertiary)]" />
            <span className={cn('flex h-14 w-14 shrink-0 items-center justify-center rounded-lg', getStepBadgeClass(step.type))}>
                <Icon className="h-6 w-6 stroke-[2.3]" />
            </span>
            <span className="min-w-0 flex-1">
                <span className="block truncate text-base font-semibold text-[var(--text-primary)]">
                    {step.label || STEP_TYPE_LABELS[step.type]}
                </span>
                <span className="mt-1 block truncate text-sm text-[var(--text-secondary)]">
                    {data.summary}
                </span>
            </span>
            <Handle type="source" position={Position.Bottom} className="!h-2.5 !w-2.5 !border-2 !border-[var(--bg-canvas)] !bg-[var(--text-tertiary)]" />
        </button>
    );
});

WorkflowCanvasNode.displayName = 'WorkflowCanvasNode';

const WorkflowActionNode = memo(function WorkflowActionNode({ data }: NodeProps<ReactFlowNode<CanvasActionNodeData>>) {
    return (
        <button
            type="button"
            onClick={data.onAction}
            className="flow-canvas-node-button surface-panel-dashed tone-action-dashed group flex w-[240px] flex-col items-start px-5 py-4 transition-gentle hover:bg-[var(--card-bg)] hover:shadow-[var(--shadow-sm)]"
        >
            <span className="text-sm font-semibold text-[var(--text-primary)]">{data.title}</span>
            <span className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">{data.detail}</span>
            <span className="tone-action-chip mt-3 inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium">
                <Plus className="h-3.5 w-3.5" />
                {data.actionLabel}
            </span>
            <Handle type="target" position={Position.Top} className="!h-2.5 !w-2.5 !border-2 !border-[var(--bg-canvas)] !bg-[var(--text-tertiary)]" />
        </button>
    );
});

WorkflowActionNode.displayName = 'WorkflowActionNode';

export const CANVAS_NODE_TYPES = {
    workflowStart: WorkflowStartNode,
    workflowStep: WorkflowCanvasNode,
    workflowAction: WorkflowActionNode,
};

export function buildCanvasGraph({
    steps,
    flowStart,
    selectedStepId,
    onSelectStep,
    onSelectStart,
    agentsById,
    functionsById,
    nodePositionOverrides,
    onAddFirstStep,
    onAddBranchStep,
    onCreateDecisionBranches,
}: {
    steps: StepNode[];
    flowStart?: FlowStart;
    selectedStepId?: string;
    onSelectStep: (stepId: string) => void;
    onSelectStart: () => void;
    agentsById: Map<string, string>;
    functionsById: Map<string, string>;
    nodePositionOverrides: Record<string, CanvasPosition>;
    onAddFirstStep: (type: StepType) => void;
    onAddBranchStep: (decisionId: string, branchId: string, type: StepType) => void;
    onCreateDecisionBranches: (decisionId: string) => void;
}): CanvasGraph {
    const nodes: CanvasReactFlowNode[] = [];
    const edges: ReactFlowEdge[] = [];
    let edgeIndex = 1;
    const startType = flowStart?.type || 'MANUAL';

    nodes.push({
        id: START_NODE_ID,
        type: 'workflowStart',
        position: { x: 360, y: 40 },
        selectable: false,
        draggable: false,
        data: {
            kind: 'start',
            startType,
            title: START_TYPE_LABELS[startType] || 'Start',
            summary: getStartSummary(flowStart),
            isSelected: selectedStepId === START_NODE_ID,
            onSelect: onSelectStart,
        },
    });

    const addEdge = (source: string, target: string, label?: string) => {
        edges.push({
            id: `canvas-edge-${edgeIndex++}`,
            source,
            target,
            label,
            type: 'smoothstep',
            animated: false,
            style: { stroke: 'color-mix(in srgb, var(--text-tertiary) 42%, transparent)', strokeWidth: 1.5 },
            labelStyle: { fill: 'var(--text-secondary)', fontSize: 11, fontWeight: 500 },
            labelBgStyle: { fill: 'var(--bg-canvas)', fillOpacity: 0.9 },
        });
    };

    const addNode = (step: StepNode, x: number, y: number) => {
        const override = nodePositionOverrides[step.id];
        nodes.push({
            id: step.id,
            type: 'workflowStep',
            position: override || { x, y },
            draggable: true,
            data: {
                step,
                summary: getStepSummary(step, agentsById, functionsById),
                isSelected: selectedStepId === step.id,
                onSelect: onSelectStep,
                kind: 'step',
            },
        });
    };

    const addActionNode = (id: string, x: number, y: number, title: string, detail: string, actionLabel: string, onAction: () => void) => {
        nodes.push({
            id,
            type: 'workflowAction',
            position: { x, y },
            selectable: false,
            draggable: false,
            data: {
                kind: 'action',
                title,
                detail,
                actionLabel,
                onAction,
            },
        });
    };

    const walk = (items: StepNode[], x: number, startY: number): { firstId: string | null; lastId: string | null; bottomY: number } => {
        let previousId: string | null = null;
        let pendingMergeSources: string[] = [];
        let firstId: string | null = null;
        let lastId: string | null = null;
        let y = startY;

        items.forEach((step) => {
            addNode(step, x, y);
            if (!firstId) firstId = step.id;
            if (pendingMergeSources.length > 0) {
                pendingMergeSources.forEach((sourceId) => addEdge(sourceId, step.id));
                pendingMergeSources = [];
            } else if (previousId) {
                addEdge(previousId, step.id);
            }

            let bottomY = y;
            if (step.type === NodeType.DECISION) {
                const branchCount = step.branches?.length || 0;
                const branchStartY = y + 170;
                const branchGap = 310;

                if (branchCount === 0) {
                    const actionId = `${step.id}__add_branches`;
                    addActionNode(
                        actionId,
                        x + 10,
                        branchStartY,
                        'Add branch paths',
                        'Create Yes / No paths so work after this decision has somewhere to go.',
                        'Add paths',
                        () => onCreateDecisionBranches(step.id)
                    );
                    addEdge(step.id, actionId);
                    bottomY = branchStartY;
                    y = bottomY + 170;
                    previousId = actionId;
                    lastId = actionId;
                    return;
                }

                const branchEndIds: string[] = [];
                (step.branches || []).forEach((branch, branchIndex) => {
                    const branchX = x + (branchIndex - (branchCount - 1) / 2) * branchGap;
                    if (branch.steps.length === 0) {
                        const actionId = `${step.id}__${branch.id}__empty`;
                        addActionNode(
                            actionId,
                            branchX,
                            branchStartY,
                            branch.label || `Branch ${branchIndex + 1}`,
                            'This path is empty. Add a step so the run can continue here.',
                            'Add step',
                            () => onAddBranchStep(step.id, branch.id, NodeType.FORM)
                        );
                        addEdge(step.id, actionId, branch.label || `Branch ${branchIndex + 1}`);
                        branchEndIds.push(actionId);
                        bottomY = Math.max(bottomY, branchStartY);
                    } else {
                        const branchGraph = walk(branch.steps, branchX, branchStartY);
                        if (branchGraph.firstId) {
                            addEdge(step.id, branchGraph.firstId, branch.label || `Branch ${branchIndex + 1}`);
                        }
                        if (branchGraph.lastId) branchEndIds.push(branchGraph.lastId);
                        bottomY = Math.max(bottomY, branchGraph.bottomY);
                    }
                });
                y = bottomY + 170;
                previousId = null;
                pendingMergeSources = branchEndIds;
                lastId = branchEndIds[0] || step.id;
            } else if (step.type === NodeType.LOOP && (step.loopSteps || []).length > 0) {
                const loopGraph = walk(step.loopSteps || [], x + 310, y + 150);
                if (loopGraph.firstId) addEdge(step.id, loopGraph.firstId, 'Loop');
                bottomY = Math.max(bottomY, loopGraph.bottomY);
                y = bottomY + 150;
                previousId = step.id;
            } else {
                y += 150;
                previousId = step.id;
            }

            lastId = step.id;
        });

        return { firstId, lastId, bottomY: Math.max(startY, y - 130) };
    };

    if (steps.length === 0) {
        const actionId = `${START_NODE_ID}__add_first`;
        addActionNode(
            actionId,
            370,
            220,
            'Add the first step',
            'Every workflow starts here, then hands off to human, AI, system, logic, timer, or done steps.',
            'Add step',
            () => onAddFirstStep(NodeType.FORM)
        );
        addEdge(START_NODE_ID, actionId);
        return { nodes, edges };
    }

    const graph = walk(steps, 360, 220);
    if (graph.firstId) addEdge(START_NODE_ID, graph.firstId);
    return { nodes, edges };
}
