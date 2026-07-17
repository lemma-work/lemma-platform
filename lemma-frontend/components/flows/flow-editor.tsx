'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    ChevronDown,
    GripVertical,
    Plus,
    Play,
    Settings,
    Trash2,
} from '@/components/ui/icons';
import {
    Background,
    BackgroundVariant,
    Controls,
    ReactFlow,
    type Edge as ReactFlowEdge,
    type FinalConnectionState,
    type OnConnectStartParams,
    type ReactFlowInstance,
} from '@xyflow/react';

import { TourLayer } from '@/components/education/coachmark';
import { ConceptHint } from '@/components/education/concept-hint';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useAgents } from '@/lib/hooks/use-agents';
import { useFunctions } from '@/lib/hooks/use-functions';
import { useTrigger } from '@/lib/hooks/use-connectors';
import { usePodMembers } from '@/lib/hooks/use-pod-members';
import { usePod } from '@/lib/hooks/use-pods';
import { cn } from '@/lib/utils';
import { FlowDefinition, FlowStart, NodeType } from '@/lib/types';

import { START_NODE_ID, START_TYPE_ICONS, START_TYPE_LABELS, STEP_TYPE_ICONS, STEP_TYPE_LABELS, STEP_TYPES } from './flow-editor-constants';
import { collectPayloadPathsFromSchema } from './flow-bindings';
import {
    addStepToBranchById,
    collectStepStats,
    createLooseStep,
    createStep,
    ensureDecisionBranchesById,
    findStepById,
    flattenStepNodes,
    hasStepId,
    insertStepAfterId,
    moveStepById,
    parseDefinition,
    removeStepById,
    serializeDefinition,
    updateStepById,
} from './flow-graph-ops';
import {
    EditorActionFooter,
    StepOwnerBadge,
    StepStatsPill,
    getAssigneeLabel,
    getNextStepSummary,
    getStartSummary,
    getStepBadgeClass,
    getStepOwnerLabel,
    getStepSummary,
    getStepSurfaceClass,
} from './flow-step-badges';
import { CANVAS_NODE_TYPES, buildCanvasGraph, type CanvasReactFlowNode } from './flow-canvas';
import {
    StartDetailsPanel,
    StepCardWorkSummary,
    StepDetailsPanel,
    type StepDetailsPanelProps,
} from './flow-node-editors';
import type {
    CanvasPosition,
    EditorSnapshot,
    EditorViewMode,
    StepNode,
    StepType,
} from './flow-editor-types';

interface FlowEditorProps {
    initialDefinition?: FlowDefinition;
    flowStart?: FlowStart;
    onSave: (definition: FlowDefinition) => void;
    onStartSave?: (start: FlowStart) => Promise<void> | void;
    viewMode?: EditorViewMode;
    onViewModeChange?: (mode: EditorViewMode) => void;
    isSaving?: boolean;
    podId: string;
}

function getClientPoint(event: MouseEvent | TouchEvent): CanvasPosition | null {
    if ('changedTouches' in event && event.changedTouches.length > 0) {
        return { x: event.changedTouches[0].clientX, y: event.changedTouches[0].clientY };
    }

    if ('touches' in event && event.touches.length > 0) {
        return { x: event.touches[0].clientX, y: event.touches[0].clientY };
    }

    if ('clientX' in event && 'clientY' in event) {
        return { x: event.clientX, y: event.clientY };
    }

    return null;
}

function isEditableEventTarget(target: EventTarget | null) {
    if (!(target instanceof HTMLElement)) return false;
    const tagName = target.tagName.toLowerCase();
    return target.isContentEditable || tagName === 'input' || tagName === 'textarea' || tagName === 'select';
}

function StepsView({
    steps,
    flowStart,
    selectedStepId,
    selectedStep,
    flattenedSteps,
    stepStats,
    agents,
    functions,
    podMembers,
    agentsById,
    functionsById,
    showStartSource,
    startPayloadFields,
    configStepId,
    isSaving,
    podId,
    onStartSave,
    onAddStep,
    onMoveSelected,
    onDeleteSelected,
    onSave,
    onUpdateSelectedStep,
    onToggleStep,
    onToggleStart,
    onToggleConfig,
}: {
    steps: StepNode[];
    flowStart?: FlowStart;
    selectedStepId?: string;
    selectedStep: StepNode | null;
    flattenedSteps: StepNode[];
    stepStats: ReturnType<typeof collectStepStats>;
    agents: StepDetailsPanelProps['agents'];
    functions: StepDetailsPanelProps['functions'];
    podMembers: StepDetailsPanelProps['podMembers'];
    agentsById: Map<string, string>;
    functionsById: Map<string, string>;
    showStartSource: boolean;
    startPayloadFields: string[];
    configStepId?: string;
    isSaving?: boolean;
    podId: string;
    onStartSave?: (start: FlowStart) => Promise<void> | void;
    onAddStep: (type: StepType) => void;
    onMoveSelected: (direction: 'up' | 'down') => void;
    onDeleteSelected: () => void;
    onSave: () => void;
    onUpdateSelectedStep: (updater: (step: StepNode) => StepNode) => void;
    onToggleStep: (stepId: string) => void;
    onToggleStart: () => void;
    onToggleConfig: (stepId: string) => void;
}) {
    const isStartSelected = selectedStepId === START_NODE_ID;
    const StartIcon = START_TYPE_ICONS[flowStart?.type || 'MANUAL'] || Play;

    return (
        <div className="flex h-full min-h-0 flex-col bg-[var(--bg-canvas)]">
            <TourLayer tour="flow-editor" />
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
                <div className="mx-auto max-w-5xl space-y-4">
                    <div data-edu="flow-start" className={cn(
                        'surface-panel tone-card-action transition-gentle',
                        isStartSelected
                            ? 'tone-card-selected'
                            : 'hover:border-[var(--tone-card-selected-border)]'
                    )}>
                        <button
                            type="button"
                            className="flow-editor-row-button flex w-full items-center gap-4 px-4 py-4"
                            onClick={onToggleStart}
                        >
                            <GripVertical className="h-4 w-4 text-[var(--text-tertiary)]" />
                            <span className="tone-action-icon flex h-14 w-14 items-center justify-center rounded-lg">
                                <StartIcon className="h-6 w-6 stroke-[2.3]" />
                            </span>
                            <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-2">
                                    <h3 className="truncate text-base font-semibold text-[var(--text-primary)]">Start</h3>
                                    <span className="tone-action-chip rounded-full px-2 py-0.5 text-xs font-medium">
                                        {START_TYPE_LABELS[flowStart?.type || 'MANUAL']}
                                    </span>
                                </div>
                                <p className="mt-1 truncate text-sm text-[var(--text-secondary)]">{getStartSummary(flowStart)}</p>
                            </div>
                            <ChevronDown className={cn('h-4 w-4 text-[var(--text-tertiary)] transition-transform', isStartSelected && 'rotate-180')} />
                        </button>
                        {isStartSelected ? (
                            <div className="border-t border-[color:color-mix(in_srgb,var(--border-subtle)_58%,transparent)] px-5 py-5">
                                <StartDetailsPanel flowStart={flowStart} onSave={onStartSave} podId={podId} />
                            </div>
                        ) : null}
                    </div>

                    {flattenedSteps.map((step) => {
                        const Icon = STEP_TYPE_ICONS[step.type];
                        const isSelected = selectedStepId === step.id;
                        const assigneeLabel = step.type === NodeType.FORM ? getAssigneeLabel(step, podMembers) : getStepOwnerLabel(step.type);
                        const nextSummary = getNextStepSummary(step, flattenedSteps);

                        return (
                            <div
                                key={step.id}
                                className={cn(
                                    'rounded-lg border transition-gentle',
                                    getStepSurfaceClass(step.type, isSelected)
                                )}
                            >
                                <button
                                    type="button"
                                    className="flow-editor-row-button flex w-full items-center gap-4 px-4 py-4"
                                    onClick={() => onToggleStep(step.id)}
                                >
                                    <GripVertical className="h-4 w-4 text-[var(--text-tertiary)]" />
                                    <span className={cn('flex h-14 w-14 shrink-0 items-center justify-center rounded-lg', getStepBadgeClass(step.type))}>
                                        <Icon className="h-6 w-6 stroke-[2.3]" />
                                    </span>
                                    <div className="min-w-0 flex-1">
                                        <div className="flex items-center gap-2">
                                            <h3 className="truncate text-base font-semibold text-[var(--text-primary)]">{step.label}</h3>
                                            <StepOwnerBadge type={step.type} />
                                        </div>
                                        <p className="mt-1 truncate text-sm text-[var(--text-secondary)]">
                                            {getStepSummary(step, agentsById, functionsById)}
                                        </p>
                                    </div>
                                    <span className="hidden max-w-[14rem] truncate text-sm font-medium text-[var(--text-secondary)] md:block">
                                        {assigneeLabel}
                                    </span>
                                    <ChevronDown className={cn('h-4 w-4 text-[var(--text-tertiary)] transition-transform', isSelected && 'rotate-180')} />
                                </button>

                                {isSelected ? (
                                    <div className="border-t border-[color:color-mix(in_srgb,var(--border-subtle)_58%,transparent)]">
                                        <div className="grid gap-3 p-4 md:grid-cols-3">
                                            <div className="rounded-lg bg-[color:color-mix(in_srgb,var(--bg-canvas)_64%,transparent)] p-4">
                                                <h4 className="text-sm font-semibold text-[var(--text-primary)]">
                                                    {step.type === NodeType.FORM ? 'What the requester sees' : 'What happens'}
                                                </h4>
                                                <div className="mt-4">
                                                    <StepCardWorkSummary step={step} agentsById={agentsById} functionsById={functionsById} />
                                                </div>
                                            </div>
                                            <div className="rounded-lg bg-[color:color-mix(in_srgb,var(--bg-canvas)_64%,transparent)] p-4">
                                                <h4 className="text-sm font-semibold text-[var(--text-primary)]">Who handles it</h4>
                                                <p className="mt-4 text-sm font-medium text-[var(--text-secondary)]">{assigneeLabel}</p>
                                                {step.type === NodeType.FORM && typeof step.config.assignee_pod_member_id_expression === 'string' && step.config.assignee_pod_member_id_expression ? (
                                                    <div className="mt-5">
                                                        <p className="text-xs text-[var(--text-tertiary)]">Dynamic rule</p>
                                                        <p className="mt-1 break-all font-mono text-xs text-[var(--state-success)]">
                                                            {step.config.assignee_pod_member_id_expression}
                                                        </p>
                                                    </div>
                                                ) : null}
                                            </div>
                                            <div className="rounded-lg bg-[color:color-mix(in_srgb,var(--bg-canvas)_64%,transparent)] p-4">
                                                <h4 className="text-sm font-semibold text-[var(--text-primary)]">When complete</h4>
                                                <p className="mt-4 text-sm text-[var(--text-tertiary)]">
                                                    {step.type === NodeType.END ? 'Run is complete' : 'Moves to'}
                                                </p>
                                                <p className="mt-2 text-sm font-medium text-[var(--text-primary)]">{nextSummary}</p>
                                            </div>
                                        </div>

                                        <div className="flex flex-wrap items-center gap-2 border-t border-[color:color-mix(in_srgb,var(--border-subtle)_46%,transparent)] px-4 py-3">
                                            <Button
                                                type="button"
                                                size="sm"
                                                variant={configStepId === step.id ? 'default' : 'outline'}
                                                className="h-8 gap-2"
                                                onClick={(event) => {
                                                    event.stopPropagation();
                                                    onToggleConfig(step.id);
                                                }}
                                            >
                                                <Settings className="h-3.5 w-3.5" />
                                                Config
                                            </Button>
                                        </div>

                                        {configStepId === step.id ? (
                                        <div className="px-5 py-5">
                                            <StepDetailsPanel
                                                step={selectedStep}
                                                allSteps={flattenedSteps}
                                                showStartSource={showStartSource}
                                                startPayloadFields={startPayloadFields}
                                                agents={agents}
                                                functions={functions}
                                                podMembers={podMembers}
                                                onUpdateStep={onUpdateSelectedStep}
                                            />
                                        </div>
                                        ) : null}
                                    </div>
                                ) : null}
                            </div>
                        );
                    })}

                    <button
                        type="button"
                        data-edu="flow-add-step"
                        onClick={() => onAddStep(NodeType.FORM)}
                        className="flow-editor-add-button flex h-14 w-full items-center justify-center rounded-lg border border-dashed border-[var(--card-border)] bg-[color:color-mix(in_srgb,var(--card-bg)_70%,transparent)] text-sm font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--action-primary)] hover:text-[var(--text-primary)]"
                    >
                        <Plus className="mr-2 h-4 w-4" />
                        Add step
                    </button>

                    {steps.length === 0 ? (
                        <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--card-bg)] p-6 text-center text-sm text-[var(--text-tertiary)]">
                            No steps yet.
                        </div>
                    ) : null}
                </div>
            </div>
            <EditorActionFooter
                stepStats={stepStats}
                selectedStep={selectedStep}
                isStartSelected={isStartSelected}
                isSaving={isSaving}
                onAddStep={onAddStep}
                onSave={onSave}
                onMoveSelected={onMoveSelected}
                onDeleteSelected={onDeleteSelected}
            />
        </div>
    );
}

export function FlowEditor({
    initialDefinition,
    flowStart,
    onSave,
    onStartSave,
    viewMode = 'steps',
    isSaving,
    podId,
}: FlowEditorProps) {
    const [steps, setSteps] = useState<StepNode[]>([]);
    const [selectedStepId, setSelectedStepId] = useState<string | undefined>(undefined);
    const [configStepId, setConfigStepId] = useState<string | undefined>(undefined);
    const [nodePositionOverrides, setNodePositionOverrides] = useState<Record<string, CanvasPosition>>({});
    // Below md the editor collapses to a view-only canvas: the palette and
    // config panes are hidden and node drag/connect gestures are disabled
    // so they don't fight one-finger panning.
    const [isCompactViewport, setIsCompactViewport] = useState(false);
    useEffect(() => {
        const mediaQuery = window.matchMedia('(max-width: 767px)');
        const sync = () => setIsCompactViewport(mediaQuery.matches);
        sync();
        mediaQuery.addEventListener('change', sync);
        return () => mediaQuery.removeEventListener('change', sync);
    }, []);
    const connectionSourceRef = useRef<string | null>(null);
    const reactFlowInstanceRef = useRef<ReactFlowInstance<CanvasReactFlowNode, ReactFlowEdge> | null>(null);
    const stepsRef = useRef<StepNode[]>([]);
    const positionsRef = useRef<Record<string, CanvasPosition>>({});
    const selectedStepIdRef = useRef<string | undefined>(undefined);
    const undoStackRef = useRef<EditorSnapshot[]>([]);
    const redoStackRef = useRef<EditorSnapshot[]>([]);
    const { data: agentsData } = useAgents(podId);
    const { data: functionsData } = useFunctions(podId);
    const { data: pod } = usePod(podId);
    // The event start config carries the app id; triggers are auth-config scoped,
    // so resolve them for that app to derive payload field paths below.
    const flowStartConnectorId =
        flowStart?.type === 'EVENT'
            ? String((flowStart.config as { connector_id?: string } | null)?.connector_id || '')
            : '';
    // The trigger list is lean (no payload_schema); fetch the selected trigger's
    // detail to derive payload field paths for autocomplete.
    const selectedTriggerId =
        flowStart?.type === 'EVENT'
            ? String((flowStart.config as { connector_trigger_id?: string } | null)?.connector_trigger_id || '')
            : '';
    const { data: selectedTrigger } = useTrigger({
        organizationId: pod?.organization_id,
        connectorId: flowStartConnectorId,
        triggerName: selectedTriggerId,
        enabled: Boolean(flowStartConnectorId && selectedTriggerId),
    });
    const { data: podMembersData } = usePodMembers(podId);

    const agents = useMemo(() => agentsData?.items || [], [agentsData]);
    const functions = useMemo(() => functionsData?.items || [], [functionsData]);
    const podMembers = useMemo(() => podMembersData?.items || [], [podMembersData]);
    const agentsById = useMemo(() => {
        const lookup = new Map<string, string>();
        agents.forEach((agent) => {
            lookup.set(agent.name, agent.name);
            lookup.set(agent.id, agent.name);
        });
        return lookup;
    }, [agents]);
    const functionsById = useMemo(() => {
        const lookup = new Map<string, string>();
        functions.forEach((fn) => {
            lookup.set(fn.id, fn.name);
            lookup.set(fn.name, fn.name);
        });
        return lookup;
    }, [functions]);

    useEffect(() => {
        stepsRef.current = steps;
    }, [steps]);

    useEffect(() => {
        positionsRef.current = nodePositionOverrides;
    }, [nodePositionOverrides]);

    useEffect(() => {
        selectedStepIdRef.current = selectedStepId;
    }, [selectedStepId]);

    const captureSnapshot = useCallback((): EditorSnapshot => ({
        steps: stepsRef.current,
        positions: positionsRef.current,
        selectedStepId: selectedStepIdRef.current,
    }), []);

    const rememberSnapshot = useCallback(() => {
        undoStackRef.current = [...undoStackRef.current.slice(-59), captureSnapshot()];
        redoStackRef.current = [];
    }, [captureSnapshot]);

    const restoreSnapshot = useCallback((snapshot: EditorSnapshot) => {
        setSteps(snapshot.steps);
        setNodePositionOverrides(snapshot.positions);
        setSelectedStepId(snapshot.selectedStepId);
    }, []);

    const undo = useCallback(() => {
        const previous = undoStackRef.current.pop();
        if (!previous) return;
        redoStackRef.current = [...redoStackRef.current.slice(-59), captureSnapshot()];
        restoreSnapshot(previous);
    }, [captureSnapshot, restoreSnapshot]);

    const redo = useCallback(() => {
        const next = redoStackRef.current.pop();
        if (!next) return;
        undoStackRef.current = [...undoStackRef.current.slice(-59), captureSnapshot()];
        restoreSnapshot(next);
    }, [captureSnapshot, restoreSnapshot]);
    const showStartSource = true;
    const startPayloadFields = useMemo(() => {
        const defaultFields = ['payload', 'metadata', 'llm_output'];

        if (flowStart?.type === 'DATASTORE_EVENT') {
            return ['payload', 'metadata', 'llm_output', 'operation', 'table_name', 'record'];
        }

        if (flowStart?.type !== 'EVENT') {
            return defaultFields;
        }

        const paths = collectPayloadPathsFromSchema(selectedTrigger?.payload_schema, 'payload');
        return Array.from(new Set([...paths, ...defaultFields]));
    }, [flowStart, selectedTrigger]);

    useEffect(() => {
        const parsed = parseDefinition(initialDefinition);
        setSteps(parsed);
        setSelectedStepId(parsed[0]?.id);
        setNodePositionOverrides({});
        undoStackRef.current = [];
        redoStackRef.current = [];
    }, [initialDefinition]);

    useEffect(() => {
        if (!selectedStepId) return;
        if (selectedStepId === START_NODE_ID) return;
        if (!hasStepId(steps, selectedStepId)) {
            setSelectedStepId(steps[0]?.id);
        }
    }, [selectedStepId, steps]);

    useEffect(() => {
        if (!configStepId) return;
        if (!hasStepId(steps, configStepId)) {
            setConfigStepId(undefined);
        }
    }, [configStepId, steps]);

    const selectedStep = useMemo(
        () => (selectedStepId ? findStepById(steps, selectedStepId) : null),
        [selectedStepId, steps]
    );
    const isStartSelected = selectedStepId === START_NODE_ID;
    const flattenedSteps = useMemo(() => flattenStepNodes(steps), [steps]);
    const stepStats = useMemo(() => collectStepStats(steps), [steps]);

    const updateSelectedStep = useCallback(
        (updater: (step: StepNode) => StepNode) => {
            if (!selectedStepId) return;
            rememberSnapshot();
            setSteps((current) => updateStepById(current, selectedStepId, updater));
        },
        [rememberSnapshot, selectedStepId]
    );

    const handleSave = useCallback(() => {
        const definition = serializeDefinition(steps);
        onSave(definition);
    }, [onSave, steps]);
    const handleAddStep = useCallback((type: StepType) => {
        const nextStep = createStep(type);
        rememberSnapshot();
        setSteps((current) => {
            if (selectedStepId === START_NODE_ID) return [nextStep, ...current];
            return selectedStepId ? insertStepAfterId(current, selectedStepId, nextStep) : [...current, nextStep];
        });
        setSelectedStepId(nextStep.id);
    }, [rememberSnapshot, selectedStepId]);
    const handleAddFirstStep = useCallback((type: StepType) => {
        const nextStep = createStep(type);
        rememberSnapshot();
        setSteps([nextStep]);
        setSelectedStepId(nextStep.id);
    }, [rememberSnapshot]);
    const handleInsertFromConnection = useCallback((sourceId: string, dropPosition?: CanvasPosition) => {
        const nextStep = createLooseStep(NodeType.FORM);
        rememberSnapshot();
        if (dropPosition) {
            setNodePositionOverrides((current) => ({
                ...current,
                [nextStep.id]: {
                    x: dropPosition.x - 125,
                    y: dropPosition.y - 36,
                },
            }));
        }

        if (sourceId === START_NODE_ID) {
            setSteps((current) => {
                if (current.length > 0) {
                    setSelectedStepId(current[0]?.id);
                    return current;
                }
                setSelectedStepId(nextStep.id);
                return [nextStep];
            });
            return;
        }

        setSteps((current) => insertStepAfterId(current, sourceId, nextStep));
        setSelectedStepId(nextStep.id);
    }, [rememberSnapshot]);
    const handleConnectStart = useCallback((_event: MouseEvent | TouchEvent, params: OnConnectStartParams) => {
        connectionSourceRef.current = params.handleType === 'source' ? params.nodeId : null;
    }, []);
    const handleConnectEnd = useCallback((event: MouseEvent | TouchEvent, connectionState: FinalConnectionState) => {
        const sourceId = connectionSourceRef.current || connectionState.fromNode?.id || null;
        connectionSourceRef.current = null;

        if (!sourceId || connectionState.toNode) return;
        const clientPoint = getClientPoint(event);
        const dropPosition = clientPoint
            ? reactFlowInstanceRef.current?.screenToFlowPosition(clientPoint)
            : undefined;
        handleInsertFromConnection(sourceId, dropPosition);
    }, [handleInsertFromConnection]);
    const handleMoveSelected = useCallback((direction: 'up' | 'down') => {
        if (!selectedStepId) return;
        rememberSnapshot();
        setSteps((current) => moveStepById(current, selectedStepId, direction));
    }, [rememberSnapshot, selectedStepId]);
    const handleCreateDecisionBranches = useCallback((decisionId: string) => {
        rememberSnapshot();
        setSteps((current) => ensureDecisionBranchesById(current, decisionId));
        setSelectedStepId(decisionId);
    }, [rememberSnapshot]);
    const handleAddBranchStep = useCallback((decisionId: string, branchId: string, type: StepType) => {
        const nextStep = createStep(type);
        rememberSnapshot();
        setSteps((current) => addStepToBranchById(current, decisionId, branchId, nextStep));
        setSelectedStepId(nextStep.id);
    }, [rememberSnapshot]);
    const handleDeleteSelected = useCallback(() => {
        if (!selectedStepId) return;
        if (selectedStepId === START_NODE_ID) return;
        let nextSelectedStepId: string | undefined;
        rememberSnapshot();
        setSteps((current) => {
            const next = removeStepById(current, selectedStepId);
            const flat = flattenStepNodes(next);
            if (flat[0]?.id) {
                nextSelectedStepId = flat[0].id;
                return next;
            }
            const fallback = createStep(NodeType.END);
            nextSelectedStepId = fallback.id;
            return [fallback];
        });
        setNodePositionOverrides((current) => {
            const next = { ...current };
            delete next[selectedStepId];
            return next;
        });
        setSelectedStepId(nextSelectedStepId);
        setConfigStepId(undefined);
    }, [rememberSnapshot, selectedStepId]);

    const handleToggleStart = useCallback(() => {
        setConfigStepId(undefined);
        setSelectedStepId((current) => current === START_NODE_ID ? undefined : START_NODE_ID);
    }, []);

    const handleToggleStep = useCallback((stepId: string) => {
        setSelectedStepId((current) => {
            if (current === stepId) {
                setConfigStepId((openStepId) => openStepId === stepId ? undefined : openStepId);
                return undefined;
            }
            return stepId;
        });
    }, []);

    const handleToggleConfig = useCallback((stepId: string) => {
        setSelectedStepId(stepId);
        setConfigStepId((current) => current === stepId ? undefined : stepId);
    }, []);

    const handleNodeDragStart = useCallback((_event: React.MouseEvent, node: CanvasReactFlowNode) => {
        if (node.type !== 'workflowStep') return;
        rememberSnapshot();
        setSelectedStepId(node.id);
    }, [rememberSnapshot]);

    const handleNodeDragStop = useCallback((_event: React.MouseEvent, node: CanvasReactFlowNode) => {
        if (node.type !== 'workflowStep') return;
        setNodePositionOverrides((current) => ({
            ...current,
            [node.id]: node.position,
        }));
        setSelectedStepId(node.id);
    }, []);

    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            if (isEditableEventTarget(event.target)) return;

            const isMod = event.metaKey || event.ctrlKey;
            const key = event.key.toLowerCase();

            if (isMod && key === 'z') {
                event.preventDefault();
                if (event.shiftKey) {
                    redo();
                } else {
                    undo();
                }
                return;
            }

            if (isMod && key === 'y') {
                event.preventDefault();
                redo();
                return;
            }

            if (event.key === 'Delete' || event.key === 'Backspace') {
                if (!selectedStepIdRef.current) return;
                event.preventDefault();
                handleDeleteSelected();
            }
        };

        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [handleDeleteSelected, redo, undo]);
    const canvasGraph = useMemo(
        // eslint-disable-next-line react-hooks/refs
        () => buildCanvasGraph({
            steps,
            flowStart,
            selectedStepId,
            onSelectStep: setSelectedStepId,
            onSelectStart: () => setSelectedStepId(START_NODE_ID),
            agentsById,
            functionsById,
            nodePositionOverrides,
            onAddFirstStep: handleAddFirstStep,
            onAddBranchStep: handleAddBranchStep,
            onCreateDecisionBranches: handleCreateDecisionBranches,
        }),
        [agentsById, flowStart, functionsById, handleAddBranchStep, handleAddFirstStep, handleCreateDecisionBranches, nodePositionOverrides, selectedStepId, steps]
    );

    if (viewMode === 'steps') {
        return (
            <StepsView
                steps={steps}
                flowStart={flowStart}
                selectedStepId={selectedStepId}
                selectedStep={selectedStep}
                flattenedSteps={flattenedSteps}
                stepStats={stepStats}
                agents={agents}
                functions={functions}
                podMembers={podMembers}
                agentsById={agentsById}
                functionsById={functionsById}
                showStartSource={showStartSource}
                startPayloadFields={startPayloadFields}
                configStepId={configStepId}
                isSaving={isSaving}
                podId={podId}
                onStartSave={onStartSave}
                onAddStep={handleAddStep}
                onMoveSelected={handleMoveSelected}
                onDeleteSelected={handleDeleteSelected}
                onSave={handleSave}
                onUpdateSelectedStep={updateSelectedStep}
                onToggleStart={handleToggleStart}
                onToggleStep={handleToggleStep}
                onToggleConfig={handleToggleConfig}
            />
        );
    }

    return (
        <div className="flex h-full min-h-0 flex-col bg-[var(--bg-canvas)]">
            <div className="surface-split-3 grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[10rem_minmax(0,1fr)_24rem]">
            <aside className="hidden min-h-0 flex-col border-r border-[color:color-mix(in_srgb,var(--border-subtle)_58%,transparent)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_74%,transparent)] px-3 py-4 md:flex">
                <Select onValueChange={(value) => handleAddStep(value as StepType)}>
                    <SelectTrigger className="h-9 bg-[var(--card-bg)] text-sm">
                        <Plus className="mr-1.5 h-4 w-4" />
                        <SelectValue placeholder="Add step" />
                    </SelectTrigger>
                    <SelectContent>
                        {STEP_TYPES.map((type) => (
                            <SelectItem key={type} value={type}>
                                {STEP_TYPE_LABELS[type]}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>

                <div className="mt-7 space-y-2">
                    {STEP_TYPES.map((type) => {
                        const Icon = STEP_TYPE_ICONS[type];
                        const hintConcept = type === NodeType.AGENT ? 'agent' : type === NodeType.FUNCTION ? 'function' : null;
                        return (
                            <div key={type} className="flex items-center gap-1">
                                <button
                                    type="button"
                                    onClick={() => handleAddStep(type)}
                                    className="flow-editor-row-button flex w-full min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-2 text-sm text-[var(--text-secondary)] transition-gentle hover:bg-[color:color-mix(in_srgb,var(--card-bg)_78%,transparent)] hover:text-[var(--text-primary)] hover:shadow-[var(--shadow-xs)]"
                                >
                                    <span className={cn('flex h-9 w-9 items-center justify-center rounded-xl', getStepBadgeClass(type))}>
                                        <Icon className="h-4 w-4 stroke-[2.3]" />
                                    </span>
                                    <span>{getStepOwnerLabel(type)}</span>
                                </button>
                                {hintConcept ? <ConceptHint concept={hintConcept} side="right" /> : null}
                            </div>
                        );
                    })}
                </div>
            </aside>

            <main className="relative min-h-0 overflow-hidden bg-[color:color-mix(in_srgb,var(--bg-canvas)_92%,white)]">
                <div className="absolute inset-x-3 top-3 z-10 rounded-lg border border-[var(--border-subtle)] bg-[var(--card-bg)] px-3 py-2 text-center text-xs text-[var(--text-secondary)] shadow-[var(--shadow-xs)] md:hidden">
                    Viewing only — open this workflow on a larger screen to edit steps.
                </div>
                <div className="absolute left-4 top-4 z-10 hidden items-center gap-2 md:flex">
                    <div className="flex items-center overflow-hidden rounded-lg border border-[var(--border-subtle)] bg-[var(--card-bg)] shadow-[var(--shadow-xs)]">
                        <button
                            type="button"
                            className="flow-editor-add-button inline-flex h-9 items-center gap-1.5 px-3 text-sm text-[var(--text-secondary)] hover:bg-[var(--surface-2)]"
                            onClick={() => handleAddStep(NodeType.FORM)}
                        >
                            <Plus className="h-3.5 w-3.5" />
                            Add after selected
                        </button>
                    </div>
                    <div className="hidden md:block">
                        <StepStatsPill stats={stepStats} />
                    </div>
                </div>

                <ReactFlow
                    nodes={canvasGraph.nodes}
                    edges={canvasGraph.edges}
                    nodeTypes={CANVAS_NODE_TYPES}
                    fitView
                    fitViewOptions={{ padding: 0.24, maxZoom: 1.08 }}
                    minZoom={0.35}
                    maxZoom={1.4}
                    nodesDraggable={!isCompactViewport}
                    nodesConnectable={!isCompactViewport}
                    elementsSelectable
                    onNodeClick={(_, node) => {
                        if (node.type === 'workflowStart') setSelectedStepId(START_NODE_ID);
                        if (node.type === 'workflowStep') setSelectedStepId(node.id);
                    }}
                    onNodeDragStart={handleNodeDragStart}
                    onNodeDragStop={handleNodeDragStop}
                    onConnectStart={handleConnectStart}
                    onConnectEnd={handleConnectEnd}
                    onInit={(instance) => {
                        reactFlowInstanceRef.current = instance;
                    }}
                    proOptions={{ hideAttribution: true }}
                    className="workflow-canvas"
                >
                    <Background variant={BackgroundVariant.Dots} gap={28} size={1.2} color="color-mix(in srgb, var(--border-subtle) 70%, transparent)" />
                    <Controls position="bottom-left" showInteractive={false} className="!m-4 !border !border-[var(--border-subtle)] !bg-[var(--card-bg)] !shadow-[var(--shadow-xs)]" />
                </ReactFlow>
            </main>

            <aside className="hidden min-h-0 overflow-y-auto border-l border-[color:color-mix(in_srgb,var(--border-subtle)_58%,transparent)] bg-[color:color-mix(in_srgb,var(--card-bg)_72%,transparent)] md:block">
                <div className="sticky top-0 z-10 border-b border-[color:color-mix(in_srgb,var(--border-subtle)_58%,transparent)] bg-[color:color-mix(in_srgb,var(--card-bg)_88%,transparent)] px-5 py-4 backdrop-blur-sm">
                    <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                            <p className="text-xs text-[var(--text-tertiary)]">
                                {isStartSelected
                                    ? 'Start'
                                    : selectedStep
                                        ? getStepOwnerLabel(selectedStep.type)
                                        : 'No step selected'}
                            </p>
                            <h2 className="mt-1 truncate text-base font-semibold text-[var(--text-primary)]">
                                {isStartSelected ? 'Start configuration' : selectedStep?.label || 'Step details'}
                            </h2>
                        </div>
                        {isStartSelected ? (
                            <span className="tone-action-chip inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium">
                                Trigger
                            </span>
                        ) : selectedStep ? <StepOwnerBadge type={selectedStep.type} /> : null}
                    </div>
                    {selectedStep && !isStartSelected ? (
                        <div className="mt-4 flex items-center gap-2">
                            <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                className="h-8 text-[var(--state-error)]"
                                onClick={handleDeleteSelected}
                            >
                                <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                                Delete
                            </Button>
                        </div>
                    ) : null}
                </div>
                <div className="px-5 py-5">
                    {isStartSelected ? (
                        <StartDetailsPanel flowStart={flowStart} onSave={onStartSave} podId={podId} />
                    ) : (
                        <StepDetailsPanel
                            step={selectedStep}
                            allSteps={flattenedSteps}
                            showStartSource={showStartSource}
                            startPayloadFields={startPayloadFields}
                            agents={agents}
                            functions={functions}
                            podMembers={podMembers}
                            onUpdateStep={updateSelectedStep}
                        />
                    )}
                        </div>
            </aside>
            </div>
            <EditorActionFooter
                stepStats={stepStats}
                selectedStep={selectedStep}
                isStartSelected={isStartSelected}
                isSaving={isSaving}
                onAddStep={handleAddStep}
                onSave={handleSave}
                onMoveSelected={handleMoveSelected}
                onDeleteSelected={handleDeleteSelected}
            />
        </div>
    );
}
