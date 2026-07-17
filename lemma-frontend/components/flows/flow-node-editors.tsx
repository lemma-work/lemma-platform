import { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, Trash2 } from '@/components/ui/icons';

import { ConceptHint } from '@/components/education/concept-hint';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { SchemaBuilder } from '@/components/agents/schema-builder';
import { useTables } from '@/lib/hooks/use-datastores';
import { useConnectors, useTriggers } from '@/lib/hooks/use-connectors';
import { usePod } from '@/lib/hooks/use-pods';
import { cn } from '@/lib/utils';
import {
    getAgentNodeName,
    getFunctionNodeName,
    setAgentNodeName,
    setFunctionNodeName,
} from '@/lib/utils/flow-node-config';
import { FlowStart, FlowStartType, NodeType } from '@/lib/types';

import { STEP_TYPE_LABELS, STEP_TYPES, DECISION_OPERATORS } from './flow-editor-constants';
import {
    buildConditionExpression,
    createConditionBuilder,
    parseConditionBuilder,
} from './flow-conditions';
import {
    getExpressionBindingValue,
    getLiteralBindingValue,
    getSchemaProperties,
    normalizeEditorInputBinding,
} from './flow-bindings';
import {
    createDefaultDecisionBranches,
    createStart,
    createStep,
    isStartReady,
    makeDefaultConfig,
    splitSourcePath,
} from './flow-graph-ops';
import {
    SectionTitle,
    StepTypeBadge,
    formatFieldType,
    getConnectorLabel,
    getStartSummary,
    getTriggerLabel,
} from './flow-step-badges';
import type {
    ConditionBuilder,
    ConditionLiteralType,
    ConditionOperand,
    ConditionOperator,
    StepBranch,
    StepNode,
    StepType,
} from './flow-editor-types';

type ConditionOperandEditorProps = {
    label: string;
    operand: ConditionOperand;
    sourceSteps: StepNode[];
    sourceStepIds: Set<string>;
    showStartSource: boolean;
    getSourceFields: (sourceId: string) => string[];
    onChange: (operand: ConditionOperand) => void;
};

function ConditionOperandEditor({
    label,
    operand,
    sourceSteps,
    sourceStepIds,
    showStartSource,
    getSourceFields,
    onChange,
}: ConditionOperandEditorProps) {
    const sourceInfo = splitSourcePath(operand.path, sourceStepIds);
    const selectedSource = sourceInfo.sourceId;
    const selectedField = sourceInfo.field;
    const availableFields = selectedSource ? getSourceFields(selectedSource) : [];

    return (
        <div className="space-y-2 rounded-lg bg-[var(--card-bg)] p-2.5 shadow-[var(--shadow-xs)]">
            <div className="flex items-center justify-between gap-2">
                <Label className="text-xs">{label}</Label>
                <div className="segmented-control border-0 bg-[var(--bg-subtle)] p-0.5">
                    <button
                        type="button"
                        data-active={operand.mode === 'path'}
                        className={cn(
                            'segmented-control-item min-h-0 min-w-0 rounded px-2 py-1 text-xs font-normal leading-normal',
                            operand.mode === 'path' ? 'bg-[var(--card-bg)] text-[var(--text-primary)]' : 'text-[var(--text-tertiary)]'
                        )}
                        onClick={() => onChange({ ...operand, mode: 'path' })}
                    >
                        Output
                    </button>
                    <button
                        type="button"
                        data-active={operand.mode === 'value'}
                        className={cn(
                            'segmented-control-item min-h-0 min-w-0 rounded px-2 py-1 text-xs font-normal leading-normal',
                            operand.mode === 'value' ? 'bg-[var(--card-bg)] text-[var(--text-primary)]' : 'text-[var(--text-tertiary)]'
                        )}
                        onClick={() => onChange({ ...operand, mode: 'value' })}
                    >
                        Value
                    </button>
                </div>
            </div>

            {operand.mode === 'path' ? (
                <div className="grid grid-cols-2 gap-2">
                    <select
                        className="form-field-control h-8 w-full px-2 text-xs"
                        value={selectedSource}
                        onChange={(event) => {
                            const nextSource = event.target.value;
                            if (!nextSource) {
                                onChange({ ...operand, path: selectedField || '' });
                                return;
                            }
                            const fields = getSourceFields(nextSource);
                            if (fields.length > 0) {
                                onChange({ ...operand, path: `${nextSource}.${fields[0]}` });
                                return;
                            }
                            onChange({ ...operand, path: nextSource });
                        }}
                    >
                        <option value="">Custom</option>
                        {showStartSource && <option value="start">Start</option>}
                        {sourceSteps.map((source) => (
                            <option key={source.id} value={source.id}>
                                {source.label || source.id}
                            </option>
                        ))}
                    </select>

                    {selectedSource && availableFields.length > 0 ? (
                        <select
                            className="form-field-control h-8 w-full px-2 text-xs"
                            value={selectedField}
                            onChange={(event) => {
                                const nextField = event.target.value;
                                onChange({
                                    ...operand,
                                    path: nextField ? `${selectedSource}.${nextField}` : selectedSource,
                                });
                            }}
                        >
                            <option value="">Whole output</option>
                            {availableFields.map((field) => (
                                <option key={field} value={field}>
                                    {field}
                                </option>
                            ))}
                        </select>
                    ) : (
                        <Input
                            value={selectedSource ? selectedField : operand.path}
                            onChange={(event) => {
                                const nextValue = event.target.value;
                                if (selectedSource) {
                                    onChange({ ...operand, path: nextValue ? `${selectedSource}.${nextValue}` : selectedSource });
                                    return;
                                }
                                onChange({ ...operand, path: nextValue });
                            }}
                            placeholder="node.output or expression"
                            className="h-8 font-mono text-xs"
                        />
                    )}
                </div>
            ) : (
                <div className="grid grid-cols-2 gap-2">
                    <select
                        className="form-field-control h-8 w-full px-2 text-xs"
                        value={operand.literalType}
                        onChange={(event) => onChange({ ...operand, literalType: event.target.value as ConditionLiteralType })}
                    >
                        <option value="string">String</option>
                        <option value="number">Number</option>
                        <option value="boolean">Boolean</option>
                        <option value="null">Null</option>
                    </select>

                    {operand.literalType === 'boolean' ? (
                        <select
                            className="form-field-control h-8 w-full px-2 text-xs"
                            value={operand.value === 'true' ? 'true' : 'false'}
                            onChange={(event) => onChange({ ...operand, value: event.target.value })}
                        >
                            <option value="true">True</option>
                            <option value="false">False</option>
                        </select>
                    ) : operand.literalType === 'null' ? (
                        <Input disabled value="None" className="h-8 text-xs" />
                    ) : (
                        <Input
                            value={operand.value}
                            onChange={(event) => onChange({ ...operand, value: event.target.value })}
                            placeholder={operand.literalType === 'number' ? '0' : 'value'}
                            className="h-8 font-mono text-xs"
                        />
                    )}
                </div>
            )}
        </div>
    );
}

type StepDetailsPanelProps = {
    step: StepNode | null;
    allSteps: StepNode[];
    showStartSource: boolean;
    startPayloadFields: string[];
    agents: Array<{
        id: string;
        name: string;
        input_schema?: Record<string, unknown>;
        output_schema?: Record<string, unknown>;
    }>;
    functions: Array<{
        id: string;
        name: string;
        input_schema?: Record<string, unknown>;
        output_schema?: Record<string, unknown>;
    }>;
    podMembers: Array<{
        pod_member_id?: string;
        user_id?: string;
        user_name?: string | null;
        user_email?: string | null;
        email?: string | null;
        role?: string;
    }>;
    onUpdateStep: (updater: (step: StepNode) => StepNode) => void;
};

export function StartDetailsPanel({
    flowStart,
    onSave,
    podId,
}: {
    flowStart?: FlowStart;
    onSave?: (start: FlowStart) => Promise<void> | void;
    podId: string;
}) {
    const effectiveStart = flowStart || createStart('MANUAL');
    const [draft, setDraft] = useState<FlowStart>(effectiveStart);
    const [isSaving, setIsSaving] = useState(false);
    const eventConfig = (draft.config || {}) as Record<string, unknown>;
    const selectedConnectorId = draft.type === 'EVENT' ? String(eventConfig.connector_id || '') : '';
    const { data: pod } = usePod(podId);
    const { data: connectors = [] } = useConnectors({ limit: 100 });
    const { data: eventTriggers = [] } = useTriggers({
        organizationId: pod?.organization_id,
        connectorId: selectedConnectorId,
        limit: 100,
        enabled: draft.type === 'EVENT' && Boolean(selectedConnectorId),
    });
    const { data: tablesData } = useTables(podId, undefined, { enabled: draft.type === 'DATASTORE_EVENT' });
    const tables = tablesData?.items || [];

    useEffect(() => {
        setDraft(flowStart || createStart('MANUAL'));
    }, [flowStart]);

    const saveDraft = async (nextDraft = draft) => {
        if (!onSave) return;
        setIsSaving(true);
        try {
            await onSave(nextDraft);
        } catch (error) {
            console.error('Failed to save start configuration:', error);
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <div className="space-y-5">
            <div>
                <div className="tone-action-chip inline-flex rounded-full px-2.5 py-1 text-xs font-medium">
                    Start
                </div>
                <h3 className="mt-3 text-base font-semibold text-[var(--text-primary)]">How this workflow starts</h3>
                <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                    Pick the trigger that creates a run, then configure the event or schedule it listens to.
                </p>
            </div>

            <div className="space-y-3 border-l border-[color:color-mix(in_srgb,var(--border-subtle)_72%,transparent)] pl-3">
                <div className="space-y-1.5">
                    <Label className="text-xs">Start type</Label>
                    <Select
                        value={draft.type}
                        onValueChange={(value) => {
                            const next = createStart(value as FlowStartType, (draft.config || {}) as Record<string, unknown>);
                            setDraft(next);
                        }}
                    >
                        <SelectTrigger>
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="MANUAL">Manual - run from Runs/API</SelectItem>
                            <SelectItem value="SCHEDULED">Scheduled - time schedule</SelectItem>
                            <SelectItem value="EVENT">Event - app/webhook trigger</SelectItem>
                            <SelectItem value="DATASTORE_EVENT">Table event - data changes</SelectItem>
                        </SelectContent>
                    </Select>
                </div>

                {draft.type === 'SCHEDULED' ? (
                    <div className="grid gap-3 rounded-lg bg-[var(--card-bg)] p-3">
                        <div className="space-y-1.5">
                            <Label className="text-xs">Schedule kind</Label>
                            <Select
                                value={String((draft.config as Record<string, unknown> | undefined)?.schedule_type || 'CRON')}
                                onValueChange={(value) => {
                                    const next = createStart('SCHEDULED', { schedule_type: value });
                                    setDraft(next);
                                }}
                            >
                                <SelectTrigger className="bg-[var(--bg-subtle)]">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="CRON">Recurring schedule</SelectItem>
                                    <SelectItem value="ONCE">One-time schedule</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                            Timing, timezone, and cadence are configured from Schedules when this workflow is installed.
                        </p>
                    </div>
                ) : null}

                {draft.type === 'EVENT' ? (
                    <div className="grid gap-3">
                        <div className="space-y-1.5">
                            <Label className="text-xs">Connector</Label>
                            <Select
                                value={selectedConnectorId}
                                onValueChange={(value) => {
                                    const next = createStart('EVENT', {
                                        connector_id: value,
                                        connector_trigger_id: '',
                                        trigger_config: {},
                                    });
                                    setDraft(next);
                                }}
                            >
                                <SelectTrigger className="bg-[var(--card-bg)]">
                                    <SelectValue placeholder="Select connector" />
                                </SelectTrigger>
                                <SelectContent>
                                    {connectors.map((connector) => (
                                        <SelectItem key={connector.id} value={connector.id}>
                                            {getConnectorLabel(connector)}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-1.5">
                            <Label className="text-xs">Trigger</Label>
                            <Select
                                value={String(eventConfig.connector_trigger_id || '')}
                                onValueChange={(value) => {
                                    const next = createStart('EVENT', {
                                        connector_id: selectedConnectorId,
                                        connector_trigger_id: value,
                                        trigger_config: eventConfig.trigger_config || {},
                                    });
                                    setDraft(next);
                                }}
                                disabled={!selectedConnectorId}
                            >
                                <SelectTrigger className="bg-[var(--card-bg)]">
                                    <SelectValue placeholder={selectedConnectorId ? 'Select trigger' : 'Select connector first'} />
                                </SelectTrigger>
                                <SelectContent>
                                    {eventTriggers.map((trigger) => (
                                        <SelectItem key={trigger.id} value={trigger.id}>
                                            {getTriggerLabel(trigger as { id: string } & Record<string, unknown>)}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>
                ) : null}

                {draft.type === 'DATASTORE_EVENT' ? (
                    <div className="grid gap-3">
                        <div className="space-y-1.5">
                            <Label className="text-xs">Table</Label>
                            <Select
                                value={String((draft.config as Record<string, unknown> | undefined)?.table_name || '')}
                                onValueChange={(value) => {
                                    const next = createStart('DATASTORE_EVENT', {
                                        ...(draft.config || {}),
                                        table_name: value,
                                    });
                                    setDraft(next);
                                }}
                            >
                                <SelectTrigger className="bg-[var(--card-bg)]">
                                    <SelectValue placeholder={tables.length ? 'Select table' : 'No tables found'} />
                                </SelectTrigger>
                                <SelectContent>
                                    {tables.map((table) => (
                                        <SelectItem key={table.name} value={table.name}>
                                            {table.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label className="text-xs">Operations</Label>
                            {(['INSERT', 'UPDATE', 'DELETE'] as const).map((operation) => {
                                const operations = Array.isArray((draft.config as Record<string, unknown> | undefined)?.operations)
                                    ? ((draft.config as { operations?: string[] }).operations || [])
                                    : [];
                                const checked = operations.includes(operation);
                                return (
                                    <label key={operation} className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                                        <input
                                            type="checkbox"
                                            checked={checked}
                                            onChange={(event) => {
                                                const nextOperations = event.target.checked
                                                    ? [...operations, operation]
                                                    : operations.filter((candidate) => candidate !== operation);
                                                const next = {
                                                    ...draft,
                                                    config: { ...(draft.config || {}), operations: nextOperations.length ? nextOperations : ['INSERT'] },
                                                };
                                                setDraft(next);
                                            }}
                                        />
                                        {operation.toLowerCase()}
                                    </label>
                                );
                            })}
                        </div>
                    </div>
                ) : null}

                {draft.type === 'MANUAL' ? (
                    <div className="rounded-lg bg-[var(--card-bg)] px-3 py-3 text-xs leading-5 text-[var(--text-tertiary)]">
                        Manual workflows start from the Runs screen or API. No external trigger is required.
                    </div>
                ) : null}
            </div>

            <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-[var(--text-tertiary)]">{getStartSummary(draft)}</span>
                <Button size="sm" onClick={() => void saveDraft()} disabled={isSaving || !isStartReady(draft)}>
                    {isSaving ? 'Saving...' : 'Save start'}
                </Button>
            </div>
        </div>
    );
}

export function StepDetailsPanel({
    step,
    allSteps,
    showStartSource,
    startPayloadFields,
    agents,
    functions,
    podMembers,
    onUpdateStep,
}: StepDetailsPanelProps) {
    const [newInputKey, setNewInputKey] = useState('');
    const isMappedStep = step?.type === NodeType.AGENT || step?.type === NodeType.FUNCTION;
    const selectedAgentRef = getAgentNodeName(step?.config);
    const selectedAgentName = useMemo(
        () => agents.find((candidate) => candidate.id === selectedAgentRef || candidate.name === selectedAgentRef)?.name || selectedAgentRef,
        [agents, selectedAgentRef]
    );
    const selectedFunctionRef = getFunctionNodeName(step?.config);
    const selectedFunctionName = useMemo(
        () => functions.find((candidate) => candidate.id === selectedFunctionRef || candidate.name === selectedFunctionRef)?.name || selectedFunctionRef,
        [functions, selectedFunctionRef]
    );
    const inputFieldRequirements = useMemo(() => {
        if (!step) return [];

        if (step.type === NodeType.AGENT) {
            const agentRef = getAgentNodeName(step.config);
            const agent = agents.find((candidate) => candidate.id === agentRef || candidate.name === agentRef);
            const fromSchema = getSchemaProperties(agent?.input_schema);
            return fromSchema.length > 0 ? fromSchema : [{ key: 'input', required: true, type: 'string' }];
        }

        if (step.type === NodeType.FUNCTION) {
            const functionRef = getFunctionNodeName(step.config);
            const fn = functions.find((candidate) => candidate.id === functionRef || candidate.name === functionRef);
            return getSchemaProperties(fn?.input_schema);
        }

        return [];
    }, [agents, functions, step]);
    const inputMappingKeys = useMemo(() => {
        if (!step || !isMappedStep) return [];

        const keys = new Set<string>(inputFieldRequirements.map((field) => field.key));
        Object.keys(step.inputs || {}).forEach((key) => keys.add(key));
        return Array.from(keys);
    }, [inputFieldRequirements, isMappedStep, step]);
    const sourceSteps = useMemo(() => {
        const unique = new Map<string, StepNode>();
        allSteps.forEach((candidate) => {
            if (candidate.id === step?.id) return;
            if (!unique.has(candidate.id)) {
                unique.set(candidate.id, candidate);
            }
        });
        return Array.from(unique.values());
    }, [allSteps, step?.id]);
    const sourceStepIds = useMemo(() => new Set(sourceSteps.map((candidate) => candidate.id)), [sourceSteps]);
    const selectedAssigneeId = typeof step?.config.assignee_pod_member_id === 'string' ? step.config.assignee_pod_member_id : '';
    const selectedAssigneeExpression = typeof step?.config.assignee_pod_member_id_expression === 'string'
        ? step.config.assignee_pod_member_id_expression
        : '';

    const updateInputValue = (key: string, value: unknown) => {
        onUpdateStep((current) => ({
            ...current,
            inputs: {
                ...current.inputs,
                [key]: value,
            },
        }));
    };

    const removeInputKey = (key: string) => {
        onUpdateStep((current) => {
            const nextInputs = { ...current.inputs };
            delete nextInputs[key];
            return {
                ...current,
                inputs: nextInputs,
            };
        });
    };

    const addInputKey = () => {
        const cleaned = newInputKey.trim();
        if (!cleaned || inputMappingKeys.includes(cleaned)) return;
        updateInputValue(cleaned, '');
        setNewInputKey('');
    };

    const getSourceFields = useCallback((sourceId: string): string[] => {
        if (sourceId === 'start') {
            return startPayloadFields.length > 0 ? startPayloadFields : ['payload'];
        }

        const source = sourceSteps.find((candidate) => candidate.id === sourceId);
        if (!source) return [];

        if (source.type === NodeType.FORM) {
            return getSchemaProperties(source.config.input_schema).map((field) => field.key);
        }

        if (source.type === NodeType.FUNCTION) {
            const functionRef = getFunctionNodeName(source.config);
            const fn = functions.find((candidate) => candidate.id === functionRef || candidate.name === functionRef);
            const fields = getSchemaProperties(fn?.output_schema).map((field) => field.key);
            return fields.length > 0 ? fields : ['result'];
        }

        if (source.type === NodeType.AGENT) {
            const agentRef = getAgentNodeName(source.config);
            const agent = agents.find((candidate) => candidate.id === agentRef || candidate.name === agentRef);
            const fields = getSchemaProperties(agent?.output_schema).map((field) => field.key);
            return fields.length > 0 ? fields : ['output'];
        }

        if (source.type === NodeType.LOOP) {
            const itemVar = String(source.config.item_var_name || 'item');
            return [itemVar, 'index'];
        }

        return [];
    }, [agents, functions, sourceSteps, startPayloadFields]);

    const updateBranch = (branchIndex: number, updater: (branch: StepBranch) => StepBranch) => {
        onUpdateStep((current) => {
            const branches = [...(current.branches || [])];
            const currentBranch = branches[branchIndex];
            if (!currentBranch) return current;
            branches[branchIndex] = updater(currentBranch);
            return { ...current, branches };
        });
    };

    const updateBranchConditionBuilder = (
        branchIndex: number,
        updater: (builder: ConditionBuilder) => ConditionBuilder
    ) => {
        updateBranch(branchIndex, (branch) => {
            const baseBuilder = branch.conditionBuilder || parseConditionBuilder(branch.condition) || createConditionBuilder();
            const nextBuilder = updater(baseBuilder);
            return {
                ...branch,
                conditionBuilder: nextBuilder,
                condition: buildConditionExpression(nextBuilder),
            };
        });
    };

    if (!step) {
        return (
            <div className="py-2">
                <h3 className="font-display text-base font-semibold leading-tight text-[var(--text-primary)]">Step work</h3>
                <p className="mt-2 text-sm leading-relaxed text-[var(--text-tertiary)]">Select a step from the left to configure it.</p>
            </div>
        );
    }

    const changeType = (type: StepType) => {
        onUpdateStep((current) => ({
            ...current,
            type,
            label: STEP_TYPE_LABELS[type],
            config: makeDefaultConfig(type),
            inputs: {},
            branches: type === NodeType.DECISION ? [] : undefined,
            loopSteps: type === NodeType.LOOP ? [] : undefined,
        }));
    };
    const loopItemsSource =
        step.type === NodeType.LOOP
            ? splitSourcePath(String(step.config.items_path || ''), sourceStepIds)
            : { sourceId: '', field: '' };
    const loopItemFields = loopItemsSource.sourceId ? getSourceFields(loopItemsSource.sourceId) : [];

    return (
        <div>
            <div className="mb-4 space-y-2 pb-2">
                <StepTypeBadge type={step.type} />
                <div>
                    <h3 className="font-display text-base font-semibold leading-tight text-[var(--text-primary)]">Step work</h3>
                    <p className="text-sm leading-relaxed text-[var(--text-tertiary)]">Set who owns this unit of work and what it needs.</p>
                </div>
            </div>

            <div className="space-y-4">
                <div className="space-y-1.5">
                    <Label className="text-xs">Label</Label>
                    <Input
                        value={step.label}
                        onChange={(event) => onUpdateStep((current) => ({ ...current, label: event.target.value }))}
                        className="bg-[color:color-mix(in_srgb,_var(--card-bg)_94%,_transparent)]"
                    />
                </div>

                <div className="space-y-1.5" data-edu="flow-step-detail">
                    <Label className="flex items-center gap-1.5 text-xs">
                        Work Type
                        {step.type === NodeType.AGENT ? <ConceptHint concept="agent" /> : null}
                        {step.type === NodeType.FUNCTION ? <ConceptHint concept="function" /> : null}
                    </Label>
                    <Select value={step.type} onValueChange={(value) => changeType(value as StepType)}>
                        <SelectTrigger className="bg-[color:color-mix(in_srgb,_var(--card-bg)_94%,_transparent)]">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            {STEP_TYPES.map((type) => (
                                <SelectItem key={type} value={type}>
                                    {STEP_TYPE_LABELS[type]}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>

                {step.type === NodeType.AGENT && (
                    <div className="space-y-3 border-l border-[color:color-mix(in_srgb,var(--border-subtle)_72%,transparent)] pl-3">
                        <div className="space-y-1.5">
                            <Label className="text-xs">Agent</Label>
                            <Select
                                value={selectedAgentName}
                                onValueChange={(value) => onUpdateStep((current) => ({ ...current, config: setAgentNodeName(current.config, value) }))}
                            >
                                <SelectTrigger>
                                    <SelectValue placeholder="Select agent" />
                                </SelectTrigger>
                                <SelectContent>
                                    {agents.map((agent) => (
                                        <SelectItem key={agent.name} value={agent.name}>
                                            {agent.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>
                )}

                {step.type === NodeType.FUNCTION && (
                    <div className="space-y-3 border-l border-[color:color-mix(in_srgb,var(--border-subtle)_72%,transparent)] pl-3">
                        <div className="space-y-1.5">
                            <Label className="text-xs">Function</Label>
                            <Select
                                value={selectedFunctionName}
                                onValueChange={(value) =>
                                    onUpdateStep((current) => ({ ...current, config: setFunctionNodeName(current.config, value) }))
                                }
                            >
                                <SelectTrigger>
                                    <SelectValue placeholder="Select function" />
                                </SelectTrigger>
                                <SelectContent>
                                    {functions.map((fn) => (
                                        <SelectItem key={fn.id} value={fn.name}>
                                            {fn.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>
                )}

                {isMappedStep && (
                    <div className="space-y-3 border-l border-[color:color-mix(in_srgb,var(--border-subtle)_72%,transparent)] pl-3">
                        <div className="flex items-center justify-between">
                            <Label className="text-xs">Input Mapping</Label>
                            <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                className="h-7 bg-[var(--card-bg)]"
                                onClick={addInputKey}
                                disabled={!newInputKey.trim() || inputMappingKeys.includes(newInputKey.trim())}
                            >
                                <Plus className="mr-1 h-3 w-3" />
                                Add
                            </Button>
                        </div>

                        <div className="flex gap-2">
                            <Input
                                value={newInputKey}
                                onChange={(event) => setNewInputKey(event.target.value)}
                                placeholder="new_input_key"
                                className="h-8 font-mono text-xs"
                            />
                        </div>

                        {inputMappingKeys.length === 0 ? (
                            <div className="rounded-lg bg-[var(--card-bg)] px-3 py-3 text-xs text-[var(--text-tertiary)]">
                                Select an agent/function first to configure its inputs.
                            </div>
                        ) : (
                            <div className="space-y-2.5">
                                {inputMappingKeys.map((key) => {
                                    const requirement = inputFieldRequirements.find((field) => field.key === key);
                                    const currentValue = step.inputs[key];
                                    const normalizedValue = normalizeEditorInputBinding(currentValue);
                                    const isStatic = normalizedValue?.type === 'literal';
                                    const staticValue = getLiteralBindingValue(currentValue);
                                    const mappedPath = getExpressionBindingValue(currentValue);
                                    const updateMappedPath = (nextPath: string) => {
                                        updateInputValue(key, { type: 'expression', value: nextPath });
                                    };
                                    const sourceInfo = splitSourcePath(mappedPath, sourceStepIds);
                                    const selectedSource = sourceInfo.sourceId;
                                    const sourceField = sourceInfo.field;
                                    const availableFields = selectedSource ? getSourceFields(selectedSource) : [];

                                    return (
                                        <div key={key} className="schema-contract-row space-y-2 px-2 py-2.5">
                                            <div className="flex items-center justify-between gap-2">
                                                <div className="flex items-center gap-2">
                                                    <span className="font-mono text-xs text-[var(--text-secondary)]">{key}</span>
                                                    {requirement?.required && <span className="text-xs text-[var(--state-error)]">*</span>}
                                                    {requirement?.type && (
                                                        <span className="rounded bg-[var(--bg-subtle)] px-1.5 py-0.5 text-xs text-[var(--text-tertiary)]">
                                                            {requirement.type}
                                                        </span>
                                                    )}
                                                </div>
                                                {!requirement && (
                                                    <Button
                                                        type="button"
                                                        variant="ghost"
                                                        size="icon"
                                                        className="h-6 w-6 text-[var(--state-error)]"
                                                        onClick={() => removeInputKey(key)}
                                                    >
                                                        <Trash2 className="h-3.5 w-3.5" />
                                                    </Button>
                                                )}
                                            </div>

                                            <div className="segmented-control w-full border-0 bg-[var(--bg-subtle)] p-0.5">
                                                <button
                                                    type="button"
                                                    data-active={!isStatic}
                                                    className={cn(
                                                        'segmented-control-item min-h-0 min-w-0 flex-1 rounded px-2 py-1 text-xs font-normal leading-normal',
                                                        !isStatic ? 'bg-[var(--card-bg)] text-[var(--text-primary)]' : 'text-[var(--text-tertiary)]'
                                                    )}
                                                    onClick={() => updateMappedPath(mappedPath)}
                                                >
                                                    Map
                                                </button>
                                                <button
                                                    type="button"
                                                    data-active={isStatic}
                                                    className={cn(
                                                        'segmented-control-item min-h-0 min-w-0 flex-1 rounded px-2 py-1 text-xs font-normal leading-normal',
                                                        isStatic ? 'bg-[var(--card-bg)] text-[var(--text-primary)]' : 'text-[var(--text-tertiary)]'
                                                    )}
                                                    onClick={() => updateInputValue(key, { type: 'literal', value: staticValue })}
                                                >
                                                    Static
                                                </button>
                                            </div>

                                            {isStatic ? (
                                                <Input
                                                    value={staticValue}
                                                    onChange={(event) => updateInputValue(key, { type: 'literal', value: event.target.value })}
                                                    placeholder="Static value or template"
                                                    className="h-8 text-xs"
                                                />
                                            ) : (
                                                <div className="grid grid-cols-2 gap-2">
                                                    <select
                                                        className="form-field-control h-8 w-full px-2 text-xs"
                                                        value={selectedSource}
                                                        onChange={(event) => {
                                                            const nextSource = event.target.value;
                                                            if (!nextSource) {
                                                                updateMappedPath('');
                                                                return;
                                                            }

                                                            const sourceFields = getSourceFields(nextSource);
                                                            if (sourceFields.length > 0) {
                                                                updateMappedPath(`${nextSource}.${sourceFields[0]}`);
                                                                return;
                                                            }

                                                            updateMappedPath(nextSource);
                                                        }}
                                                    >
                                                        <option value="">Select source</option>
                                                        {showStartSource && <option value="start">Start</option>}
                                                        {sourceSteps.map((source) => (
                                                            <option key={source.id} value={source.id}>
                                                                {source.label || source.id}
                                                            </option>
                                                        ))}
                                                    </select>

                                                    {availableFields.length > 0 ? (
                                                        <select
                                                            className="form-field-control h-8 w-full px-2 text-xs"
                                                            value={sourceField}
                                                            disabled={!selectedSource}
                                                            onChange={(event) => {
                                                                const nextField = event.target.value;
                                                                if (!selectedSource) return;
                                                                updateMappedPath(
                                                                    nextField ? `${selectedSource}.${nextField}` : selectedSource
                                                                );
                                                            }}
                                                        >
                                                            <option value="">Whole output</option>
                                                            {availableFields.map((field) => (
                                                                <option key={field} value={field}>
                                                                    {field}
                                                                </option>
                                                            ))}
                                                        </select>
                                                    ) : (
                                                        <Input
                                                            value={sourceField}
                                                            disabled={!selectedSource}
                                                            onChange={(event) => {
                                                                if (!selectedSource) return;
                                                                const nextField = event.target.value;
                                                                updateMappedPath(
                                                                    nextField ? `${selectedSource}.${nextField}` : selectedSource
                                                                );
                                                            }}
                                                            placeholder="field.path"
                                                            className="h-8 font-mono text-xs"
                                                        />
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                )}

                {step.type === NodeType.WAIT_UNTIL && (
                    <div className="space-y-1.5 border-l border-[color:color-mix(in_srgb,var(--border-subtle)_72%,transparent)] pl-3">
                        <Label className="text-xs">Timeout (seconds)</Label>
                        <Input
                            type="number"
                            value={String(step.config.timeout_seconds || '')}
                            className="bg-transparent"
                            onChange={(event) =>
                                onUpdateStep((current) => ({
                                    ...current,
                                    config: { ...current.config, timeout_seconds: Number(event.target.value) || 0 },
                                }))
                            }
                        />
                    </div>
                )}

                {step.type === NodeType.LOOP && (
                    <div className="space-y-3 border-l border-[color:color-mix(in_srgb,var(--border-subtle)_72%,transparent)] pl-3">
                        <div className="space-y-1.5">
                            <Label className="text-xs">Items Path</Label>
                            <div className="grid grid-cols-2 gap-2">
                                <select
                                    className="form-field-control h-8 w-full px-2 text-xs"
                                    value={loopItemsSource.sourceId}
                                    onChange={(event) => {
                                        const nextSource = event.target.value;
                                        if (!nextSource) {
                                            onUpdateStep((current) => ({
                                                ...current,
                                                config: { ...current.config, items_path: loopItemsSource.field || '' },
                                            }));
                                            return;
                                        }

                                        const fields = getSourceFields(nextSource);
                                        onUpdateStep((current) => ({
                                            ...current,
                                            config: {
                                                ...current.config,
                                                items_path: fields.length > 0 ? `${nextSource}.${fields[0]}` : nextSource,
                                            },
                                        }));
                                    }}
                                >
                                    <option value="">Custom</option>
                                    {showStartSource && <option value="start">Start</option>}
                                    {sourceSteps.map((source) => (
                                        <option key={source.id} value={source.id}>
                                            {source.label || source.id}
                                        </option>
                                    ))}
                                </select>

                                {loopItemsSource.sourceId && loopItemFields.length > 0 ? (
                                    <select
                                        className="form-field-control h-8 w-full px-2 text-xs"
                                        value={loopItemsSource.field}
                                        onChange={(event) => {
                                            const nextField = event.target.value;
                                            onUpdateStep((current) => ({
                                                ...current,
                                                config: {
                                                    ...current.config,
                                                    items_path: nextField
                                                        ? `${loopItemsSource.sourceId}.${nextField}`
                                                        : loopItemsSource.sourceId,
                                                },
                                            }));
                                        }}
                                    >
                                        <option value="">Whole output</option>
                                        {loopItemFields.map((field) => (
                                            <option key={field} value={field}>
                                                {field}
                                            </option>
                                        ))}
                                    </select>
                                ) : (
                                    <Input
                                        value={loopItemsSource.sourceId ? loopItemsSource.field : String(step.config.items_path || '')}
                                        placeholder="node.output.items"
                                        className="h-8 bg-[var(--card-bg)] font-mono text-xs"
                                        onChange={(event) =>
                                            onUpdateStep((current) => ({
                                                ...current,
                                                config: {
                                                    ...current.config,
                                                    items_path: loopItemsSource.sourceId
                                                        ? (event.target.value
                                                            ? `${loopItemsSource.sourceId}.${event.target.value}`
                                                            : loopItemsSource.sourceId)
                                                        : event.target.value,
                                                },
                                            }))
                                        }
                                    />
                                )}
                            </div>
                        </div>
                        <div className="space-y-1.5">
                            <Label className="text-xs">Item Variable</Label>
                            <Input
                                value={String(step.config.item_var_name || 'item')}
                                className="bg-[var(--card-bg)]"
                                onChange={(event) =>
                                    onUpdateStep((current) => ({
                                        ...current,
                                        config: { ...current.config, item_var_name: event.target.value || 'item' },
                                    }))
                                }
                            />
                        </div>
                    </div>
                )}

                {step.type === NodeType.DECISION && (
                    <div className="space-y-2.5 border-l border-[color:color-mix(in_srgb,var(--border-subtle)_72%,transparent)] pl-3">
                        <div className="flex items-center justify-between">
                            <SectionTitle title="Branches" />
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="h-7 bg-[var(--card-bg)]"
                                onClick={() =>
                                    onUpdateStep((current) => {
                                        const branches = current.branches || [];
                                        const condition = branches.length === 0 ? '1 == 1' : '';
                                        return {
                                            ...current,
                                            branches: [
                                                ...branches,
                                                {
                                                    id: `${current.id}_branch_${branches.length + 1}`,
                                                    label: `Branch ${branches.length + 1}`,
                                                    condition,
                                                    conditionBuilder: parseConditionBuilder(condition) || createConditionBuilder(),
                                                    steps: [],
                                                },
                                            ],
                                        };
                                    })
                                }
                            >
                                <Plus className="mr-1 h-3 w-3" />
                                Add
                            </Button>
                        </div>

                        {(step.branches || []).length === 0 ? (
                            <div className="space-y-3 rounded-md bg-[color:color-mix(in_srgb,var(--surface-2)_34%,transparent)] px-3 py-3 text-xs text-[var(--text-tertiary)]">
                                <p>No branches yet. Add paths before relying on this decision.</p>
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    className="h-7 bg-[var(--card-bg)]"
                                    onClick={() =>
                                        onUpdateStep((current) => ({
                                            ...current,
                                            branches: createDefaultDecisionBranches(current.id),
                                        }))
                                    }
                                >
                                    <Plus className="mr-1 h-3 w-3" />
                                    Add Yes / No paths
                                </Button>
                            </div>
                        ) : (
                            (step.branches || []).map((branch, index) => (
                                <div key={branch.id} className="schema-contract-row space-y-2 px-2 py-2.5">
                                    <div className="space-y-1.5">
                                        <Label className="text-xs">Branch Label</Label>
                                        <Input
                                            value={branch.label}
                                            onChange={(event) => updateBranch(index, (currentBranch) => ({ ...currentBranch, label: event.target.value }))}
                                        />
                                    </div>

                                    {(() => {
                                        const parsedBuilder = branch.conditionBuilder || parseConditionBuilder(branch.condition);
                                        if (!parsedBuilder) {
                                            return (
                                                <div className="space-y-1.5">
                                                    <Label className="text-xs">Condition (Raw)</Label>
                                                    <Input
                                                        className="font-mono text-xs"
                                                        value={branch.condition}
                                                        placeholder="1 == 1"
                                                        onChange={(event) => {
                                                            const nextCondition = event.target.value;
                                                            updateBranch(index, (currentBranch) => ({
                                                                ...currentBranch,
                                                                condition: nextCondition,
                                                                conditionBuilder: parseConditionBuilder(nextCondition) || undefined,
                                                            }));
                                                        }}
                                                    />
                                                    <p className="text-xs text-[var(--text-tertiary)]">
                                                        Use raw mode for advanced expressions. Simple expressions can use the builder.
                                                    </p>
                                                </div>
                                            );
                                        }

                                        const builder = parsedBuilder;
                                        return (
                                            <div className="space-y-2">
                                                <Label className="text-xs">Condition Builder</Label>
                                                <ConditionOperandEditor
                                                    label="Left Operand"
                                                    operand={builder.left}
                                                    sourceSteps={sourceSteps}
                                                    sourceStepIds={sourceStepIds}
                                                    showStartSource={showStartSource}
                                                    getSourceFields={getSourceFields}
                                                    onChange={(nextOperand) =>
                                                        updateBranchConditionBuilder(index, (currentBuilder) => ({
                                                            ...currentBuilder,
                                                            left: nextOperand,
                                                        }))
                                                    }
                                                />
                                                <div className="space-y-1">
                                                    <Label className="text-xs">Operator</Label>
                                                    <select
                                                        className="form-field-control h-8 w-full px-2 text-xs"
                                                        value={builder.operator}
                                                        onChange={(event) =>
                                                            updateBranchConditionBuilder(index, (currentBuilder) => ({
                                                                ...currentBuilder,
                                                                operator: event.target.value as ConditionOperator,
                                                            }))
                                                        }
                                                    >
                                                        {DECISION_OPERATORS.map((operator) => (
                                                            <option key={operator} value={operator}>
                                                                {operator}
                                                            </option>
                                                        ))}
                                                    </select>
                                                </div>
                                                <ConditionOperandEditor
                                                    label="Right Operand"
                                                    operand={builder.right}
                                                    sourceSteps={sourceSteps}
                                                    sourceStepIds={sourceStepIds}
                                                    showStartSource={showStartSource}
                                                    getSourceFields={getSourceFields}
                                                    onChange={(nextOperand) =>
                                                        updateBranchConditionBuilder(index, (currentBuilder) => ({
                                                            ...currentBuilder,
                                                            right: nextOperand,
                                                        }))
                                                    }
                                                />
                                                <div className="rounded-md bg-[var(--bg-subtle)] px-2.5 py-1.5">
                                                    <p className="type-eyebrow-medium">
                                                        Python Expression
                                                    </p>
                                                    <p className="mt-1 whitespace-pre-wrap break-all font-mono text-xs text-[var(--text-secondary)]">
                                                        {buildConditionExpression(builder)}
                                                    </p>
                                                </div>
                                            </div>
                                        );
                                    })()}
                                    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--text-tertiary)]">
                                        <div className="flex items-center gap-2">
                                            <span>{branch.steps.length} steps</span>
                                            <Button
                                                type="button"
                                                variant="outline"
                                                size="sm"
                                                className="h-7 bg-[var(--card-bg)]"
                                                onClick={() =>
                                                    onUpdateStep((current) => {
                                                        const branches = [...(current.branches || [])];
                                                        const currentBranch = branches[index];
                                                        if (!currentBranch) return current;
                                                        branches[index] = {
                                                            ...currentBranch,
                                                            steps: [...currentBranch.steps, createStep(NodeType.FORM)],
                                                        };
                                                        return { ...current, branches };
                                                    })
                                                }
                                            >
                                                <Plus className="mr-1 h-3 w-3" />
                                                Add step
                                            </Button>
                                        </div>
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="sm"
                                            className="h-7 text-[var(--state-error)]"
                                            onClick={() =>
                                                onUpdateStep((current) => ({
                                                    ...current,
                                                    branches: (current.branches || []).filter((_, branchIndex) => branchIndex !== index),
                                                }))
                                            }
                                        >
                                            <Trash2 className="mr-1 h-3 w-3" />
                                            Remove
                                        </Button>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                )}

                {step.type === NodeType.FORM && (
                    <div className="space-y-3">
                        <div className="space-y-3 border-l border-[color:color-mix(in_srgb,var(--border-subtle)_72%,transparent)] pl-3">
                            <div className="space-y-1.5">
                                <Label className="text-xs">Assignee</Label>
                                <Select
                                    value={selectedAssigneeId || '__unassigned__'}
                                    onValueChange={(value) =>
                                        onUpdateStep((current) => {
                                            const nextConfig = { ...current.config };
                                            if (value === '__unassigned__') {
                                                delete nextConfig.assignee_pod_member_id;
                                            } else {
                                                nextConfig.assignee_pod_member_id = value;
                                            }
                                            return { ...current, config: nextConfig };
                                        })
                                    }
                                >
                                    <SelectTrigger className="h-8">
                                        <SelectValue placeholder="Choose who should answer" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="__unassigned__">Anyone with access</SelectItem>
                                        {podMembers.map((member) => {
                                            const memberId = member.pod_member_id || '';
                                            if (!memberId) return null;
                                            const label = member.user_name || member.user_email || member.email || memberId;
                                            return (
                                                <SelectItem key={memberId} value={memberId}>
                                                    {label}
                                                </SelectItem>
                                            );
                                        })}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-1.5">
                                <Label className="text-xs">Assignee expression</Label>
                                <Input
                                    value={selectedAssigneeExpression}
                                    placeholder="payload.owner_pod_member_id"
                                    className="h-8 font-mono text-xs"
                                    onChange={(event) =>
                                        onUpdateStep((current) => {
                                            const nextConfig = { ...current.config };
                                            const value = event.target.value.trim();
                                            if (value) {
                                                nextConfig.assignee_pod_member_id_expression = value;
                                            } else {
                                                delete nextConfig.assignee_pod_member_id_expression;
                                            }
                                            return { ...current, config: nextConfig };
                                        })
                                    }
                                />
                                <p className="text-xs leading-4 text-[var(--text-tertiary)]">
                                    Expression wins over the fixed assignee when both are set.
                                </p>
                            </div>
                        </div>
                        <div className="space-y-2 border-l border-[color:color-mix(in_srgb,var(--border-subtle)_72%,transparent)] pl-3">
                            <Label className="text-xs">Form Fields</Label>
                            <SchemaBuilder
                                value={(step.config.input_schema as Record<string, unknown>) || { type: 'object', properties: {} }}
                                onChange={(schema) =>
                                    onUpdateStep((current) => ({
                                        ...current,
                                        config: { ...current.config, input_schema: schema },
                                    }))
                                }
                            />
                        </div>
                    </div>
                )}

                {step.type === NodeType.END && (
                    <div className="rounded-lg bg-[color:color-mix(in_srgb,_var(--bg-subtle)_58%,_transparent)] px-3 py-2 text-xs text-[var(--text-tertiary)]">
                        This step ends the workflow run.
                    </div>
                )}
            </div>
        </div>
    );
}

export function StepCardWorkSummary({
    step,
    agentsById,
    functionsById,
}: {
    step: StepNode;
    agentsById: Map<string, string>;
    functionsById: Map<string, string>;
}) {
    if (step.type === NodeType.FORM) {
        const fields = getSchemaProperties(step.config.input_schema);
        if (fields.length === 0) {
            return <p className="text-sm text-[var(--text-tertiary)]">No fields yet.</p>;
        }

        return (
            <ul className="space-y-2 text-sm text-[var(--text-secondary)]">
                {fields.slice(0, 3).map((field) => (
                    <li key={field.key} className="flex items-start gap-2">
                        <span className="mt-2 h-1 w-1 rounded-full bg-[var(--text-tertiary)]" />
                        <span>{field.key} <span className="text-[var(--text-tertiary)]">({formatFieldType(field.type)})</span></span>
                    </li>
                ))}
                {fields.length > 3 ? (
                    <li className="text-[var(--text-tertiary)]">+ {fields.length - 3} more field{fields.length - 3 === 1 ? '' : 's'}</li>
                ) : null}
            </ul>
        );
    }

    if (step.type === NodeType.AGENT) {
        const agentName = agentsById.get(getAgentNodeName(step.config)) || getAgentNodeName(step.config);
        const inputs = Object.keys(step.inputs || {});
        return (
            <div className="space-y-2 text-sm text-[var(--text-secondary)]">
                <p>{agentName ? `Agent: ${agentName}` : 'No agent selected'}</p>
                <p className="text-xs text-[var(--text-tertiary)]">
                    {inputs.length ? `${inputs.length} mapped input${inputs.length === 1 ? '' : 's'}` : 'No input mapping yet'}
                </p>
            </div>
        );
    }

    if (step.type === NodeType.FUNCTION) {
        const functionName = functionsById.get(getFunctionNodeName(step.config)) || getFunctionNodeName(step.config);
        const inputs = Object.keys(step.inputs || {});
        return (
            <div className="space-y-2 text-sm text-[var(--text-secondary)]">
                <p>{functionName ? `Function: ${functionName}` : 'No function selected'}</p>
                <p className="text-xs text-[var(--text-tertiary)]">
                    {inputs.length ? `${inputs.length} mapped input${inputs.length === 1 ? '' : 's'}` : 'No input mapping yet'}
                </p>
            </div>
        );
    }

    if (step.type === NodeType.DECISION) {
        const branches = step.branches || [];
        return branches.length ? (
            <ul className="space-y-2 text-sm text-[var(--text-secondary)]">
                {branches.map((branch) => (
                    <li key={branch.id} className="flex items-start gap-2">
                        <span className="mt-2 h-1 w-1 rounded-full bg-[var(--state-warning)]" />
                        <span>{branch.label}: <span className="font-mono text-xs text-[var(--text-tertiary)]">{branch.condition || 'condition not set'}</span></span>
                    </li>
                ))}
            </ul>
        ) : <p className="text-sm text-[var(--text-tertiary)]">No branches yet.</p>;
    }

    if (step.type === NodeType.LOOP) {
        return <p className="font-mono text-sm text-[var(--text-secondary)]">{String(step.config.items_path || 'items path not set')}</p>;
    }

    if (step.type === NodeType.WAIT_UNTIL) {
        return <p className="text-sm text-[var(--text-secondary)]">Waits {Number(step.config.timeout_seconds || 0)} seconds.</p>;
    }

    return <p className="text-sm text-[var(--text-secondary)]">Finish workflow.</p>;
}

export type { StepDetailsPanelProps };
