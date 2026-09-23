import { ScheduledWorkflowStartConfigType } from 'lemma-sdk';

import {
    getAgentNodeName,
    getFunctionNodeName,
    normalizeFlowNodeConfig,
} from '@/lib/utils/flow-node-config';
import { FlowDefinition, FlowEdge, FlowNode, FlowStart, FlowStartType, NodeType } from '@/lib/types';

import { STEP_TYPE_LABELS } from './flow-editor-constants';
import { getBranchCondition, parseConditionBuilder } from './flow-conditions';
import { normalizeEditorInputBindings, serializeInputBindingsForContract } from './flow-bindings';
import type { ParseOptions, StepBranch, StepNode, StepStats, StepType } from './flow-editor-types';

let sequence = 0;
const getId = () => `step_${Date.now()}_${sequence++}`;

export function makeDefaultConfig(type: StepType): Record<string, unknown> {
    if (type === NodeType.FORM) {
        return {
            input_schema: {
                type: 'object',
                properties: {},
            },
        };
    }
    if (type === NodeType.WAIT_UNTIL) {
        return {
            timeout_seconds: 300,
        };
    }
    if (type === NodeType.LOOP) {
        return {
            items_path: '',
            item_var_name: 'item',
        };
    }
    return {};
}

export function splitSourcePath(path: string, sourceIds: Set<string>): { sourceId: string; field: string } {
    const trimmed = path.trim();
    if (!trimmed) {
        return { sourceId: '', field: '' };
    }

    if (trimmed.startsWith('flow_start.')) {
        return { sourceId: 'start', field: trimmed.slice('flow_start.'.length) };
    }

    if (trimmed.startsWith('start.')) {
        return { sourceId: 'start', field: trimmed.slice('start.'.length) };
    }

    const dotIndex = trimmed.indexOf('.');
    if (dotIndex > 0) {
        const sourceId = trimmed.slice(0, dotIndex);
        if (sourceIds.has(sourceId)) {
            return {
                sourceId,
                field: trimmed.slice(dotIndex + 1),
            };
        }
    }

    if (sourceIds.has(trimmed)) {
        return { sourceId: trimmed, field: '' };
    }

    return { sourceId: '', field: trimmed };
}

export function createStep(type: StepType): StepNode {
    return {
        id: getId(),
        type,
        label: STEP_TYPE_LABELS[type],
        config: makeDefaultConfig(type),
        inputs: {},
        branches: type === NodeType.DECISION ? [] : undefined,
        loopSteps: type === NodeType.LOOP ? [] : undefined,
    };
}

export function createLooseStep(type: StepType = NodeType.FORM): StepNode {
    return {
        ...createStep(type),
        label: 'New step',
    };
}

function createBranch(decisionId: string, index: number, overrides: Partial<StepBranch> = {}): StepBranch {
    const condition = typeof overrides.condition === 'string' ? overrides.condition : '';
    return {
        id: overrides.id || `${decisionId}_branch_${index + 1}_${Date.now()}`,
        label: overrides.label || `Branch ${index + 1}`,
        condition,
        conditionBuilder: overrides.conditionBuilder || parseConditionBuilder(condition) || undefined,
        steps: overrides.steps || [],
    };
}

export function createDefaultDecisionBranches(decisionId: string): StepBranch[] {
    return [
        createBranch(decisionId, 0, { label: 'Yes' }),
        createBranch(decisionId, 1, { label: 'No', condition: '1 == 1' }),
    ];
}

function toStepNode(node: FlowNode): StepNode {
    const config = normalizeFlowNodeConfig(node.type, node.config || {});
    delete config.layout;

    const isMappedType = node.type === NodeType.AGENT || node.type === NodeType.FUNCTION;
    const inputMapping =
        isMappedType && typeof config.input_mapping === 'object' && config.input_mapping
            ? normalizeEditorInputBindings(config.input_mapping as Record<string, unknown>)
            : {};
    delete config.input_mapping;

    if (node.type === NodeType.DECISION) {
        delete config.rules;
        delete config.paths;
    }

    return {
        id: node.id,
        type: node.type as StepType,
        label: node.label || STEP_TYPE_LABELS[node.type as StepType] || node.type,
        config,
        inputs: inputMapping,
        branches: node.type === NodeType.DECISION ? [] : undefined,
        loopSteps: node.type === NodeType.LOOP ? [] : undefined,
    };
}

function parseChain(startId: string, options: ParseOptions): { steps: StepNode[]; nextId: string | null } {
    const localVisited = new Set<string>();
    const steps: StepNode[] = [];
    let currentId: string | null = startId;

    while (currentId) {
        if (currentId !== startId && options.stopAtIds?.has(currentId)) {
            return { steps, nextId: currentId };
        }

        if (options.stopAtMerge && currentId !== startId && (options.incoming.get(currentId) || 0) > 1) {
            return { steps, nextId: currentId };
        }

        if (localVisited.has(currentId)) {
            return { steps, nextId: null };
        }

        const node = options.nodeMap.get(currentId);
        if (!node) {
            return { steps, nextId: null };
        }

        if (options.markIncluded) {
            if (options.included.has(currentId)) {
                return { steps, nextId: currentId };
            }
            options.included.add(currentId);
        }

        localVisited.add(currentId);
        const step = toStepNode(node);
        const outgoing = options.outgoing.get(currentId) || [];

        if (step.type === NodeType.DECISION) {
            const rawRules = Array.isArray(node.config?.rules) ? node.config.rules : [];
            const rawPaths =
                node.config
                && typeof node.config === 'object'
                && Array.isArray((node.config as { paths?: unknown[] }).paths)
                    ? (node.config as { paths: unknown[] }).paths
                    : [];
            const mergeCandidates: string[] = [];

            step.branches = outgoing.map((target, index) => {
                const parsed = parseChain(target, {
                    ...options,
                    markIncluded: false,
                    stopAtMerge: true,
                });
                if (parsed.nextId) mergeCandidates.push(parsed.nextId);

                const rule = rawRules[index] as { condition?: string } | undefined;
                const path = rawPaths[index] as { label?: string; condition?: string } | undefined;
                const condition = rule?.condition || path?.condition || '1 == 1';
                const label = typeof path?.label === 'string' && path.label.trim() ? path.label.trim() : `Branch ${index + 1}`;
                return {
                    id: `${step.id}_branch_${index + 1}`,
                    label,
                    condition,
                    conditionBuilder: parseConditionBuilder(condition) || undefined,
                    steps: parsed.steps,
                };
            });

            steps.push(step);

            const commonMerge =
                mergeCandidates.length > 0 && mergeCandidates.every((id) => id === mergeCandidates[0])
                    ? mergeCandidates[0]
                    : null;
            currentId = commonMerge;
            continue;
        }

        if (step.type === NodeType.LOOP) {
            const childId = typeof step.config.child_node_id === 'string' ? step.config.child_node_id : '';
            if (childId && options.nodeMap.has(childId)) {
                const childParsed = parseChain(childId, {
                    ...options,
                    markIncluded: false,
                    stopAtMerge: false,
                    stopAtIds: new Set([step.id]),
                });
                step.loopSteps = childParsed.steps;
            } else {
                step.loopSteps = [];
            }

            steps.push(step);
            currentId = outgoing[0] || null;
            continue;
        }

        steps.push(step);
        currentId = outgoing[0] || null;
    }

    return { steps, nextId: null };
}

function collectStepIds(steps: StepNode[]): Set<string> {
    const ids = new Set<string>();
    const walk = (nodes: StepNode[]) => {
        nodes.forEach((step) => {
            ids.add(step.id);
            if (step.branches) {
                step.branches.forEach((branch) => walk(branch.steps));
            }
            if (step.loopSteps) {
                walk(step.loopSteps);
            }
        });
    };
    walk(steps);
    return ids;
}

function isConfiguredStep(step: StepNode): boolean {
    if (step.type === NodeType.AGENT) {
        return Boolean(getAgentNodeName(step.config));
    }
    if (step.type === NodeType.FUNCTION) {
        return Boolean(getFunctionNodeName(step.config));
    }
    if (step.type === NodeType.DECISION) {
        return (step.branches?.length || 0) > 0;
    }
    if (step.type === NodeType.LOOP) {
        return Boolean(step.config.items_path) && (step.loopSteps?.length || 0) > 0;
    }
    if (step.type === NodeType.WAIT_UNTIL) {
        return Number(step.config.timeout_seconds || 0) > 0;
    }
    if (step.type === NodeType.FORM) {
        const schema = step.config.input_schema as { properties?: Record<string, unknown> } | undefined;
        return Boolean(schema?.properties && Object.keys(schema.properties).length > 0);
    }
    return true;
}

export function collectStepStats(steps: StepNode[]): StepStats {
    const stats: StepStats = {
        total: 0,
        configured: 0,
        branchCount: 0,
    };

    const walk = (nodes: StepNode[]) => {
        nodes.forEach((step) => {
            stats.total += 1;
            if (isConfiguredStep(step)) {
                stats.configured += 1;
            }

            if (step.type === NodeType.DECISION) {
                stats.branchCount += step.branches?.length || 0;
                step.branches?.forEach((branch) => walk(branch.steps));
            }

            if (step.loopSteps) {
                walk(step.loopSteps);
            }
        });
    };

    walk(steps);
    return stats;
}

export function flattenStepNodes(steps: StepNode[]): StepNode[] {
    const flat: StepNode[] = [];

    const walk = (nodes: StepNode[]) => {
        nodes.forEach((step) => {
            flat.push(step);
            if (step.branches) {
                step.branches.forEach((branch) => walk(branch.steps));
            }
            if (step.loopSteps) {
                walk(step.loopSteps);
            }
        });
    };

    walk(steps);
    return flat;
}

export function parseDefinition(definition?: FlowDefinition): StepNode[] {
    if (!definition || !Array.isArray(definition.nodes) || definition.nodes.length === 0) {
        return [createStep(NodeType.END)];
    }

    const nodes = definition.nodes.filter((node) => node.type !== NodeType.START);
    if (nodes.length === 0) {
        return [createStep(NodeType.END)];
    }

    const nodeOrder = new Map(nodes.map((node, index) => [node.id, index]));
    const nodeMap = new Map(nodes.map((node) => [node.id, node]));
    const outgoing = new Map<string, string[]>();
    const incoming = new Map<string, number>();

    nodes.forEach((node) => {
        outgoing.set(node.id, []);
        incoming.set(node.id, 0);
    });

    (definition.edges || []).forEach((edge) => {
        if (!nodeMap.has(edge.source) || !nodeMap.has(edge.target)) return;
        outgoing.get(edge.source)?.push(edge.target);
        incoming.set(edge.target, (incoming.get(edge.target) || 0) + 1);
    });

    const loopChildIds = new Set<string>();
    nodes.forEach((node) => {
        if (node.type === NodeType.LOOP && typeof node.config?.child_node_id === 'string') {
            loopChildIds.add(node.config.child_node_id as string);
        }
    });

    const roots = nodes
        .filter((node) => (incoming.get(node.id) || 0) === 0 && !loopChildIds.has(node.id))
        .sort((a, b) => (nodeOrder.get(a.id) || 0) - (nodeOrder.get(b.id) || 0));
    const startId = roots[0]?.id || nodes[0].id;

    const included = new Set<string>();
    const parsed = parseChain(startId, {
        nodeMap,
        outgoing,
        incoming,
        included,
        markIncluded: true,
        stopAtMerge: false,
    });

    const mainSteps = parsed.steps.length > 0 ? parsed.steps : [toStepNode(nodes[0])];
    const knownIds = collectStepIds(mainSteps);

    const leftovers = nodes.filter((node) => !knownIds.has(node.id) && !loopChildIds.has(node.id));
    if (leftovers.length === 0) {
        return mainSteps;
    }

    const extra = [...mainSteps];
    leftovers.forEach((node) => {
        const branch = parseChain(node.id, {
            nodeMap,
            outgoing,
            incoming,
            included,
            markIncluded: true,
            stopAtMerge: false,
        });
        if (branch.steps.length > 0) {
            extra.push(...branch.steps);
        }
    });

    return extra;
}

export function serializeDefinition(steps: StepNode[]): FlowDefinition {
    const nodes: FlowNode[] = [];
    const edges: FlowEdge[] = [];
    const addedNodes = new Set<string>();
    let edgeCounter = 1;

    const addEdge = (source: string, target: string, label?: string) => {
        edges.push({
            id: `e_${edgeCounter++}`,
            source,
            target,
            edge_type: 'default',
            label,
        });
    };

    const visitStep = (step: StepNode, nextId: string | null) => {
        if (!addedNodes.has(step.id)) {
            const config = normalizeFlowNodeConfig(step.type, step.config);
            if ((step.type === NodeType.AGENT || step.type === NodeType.FUNCTION) && Object.keys(step.inputs).length > 0) {
                config.input_mapping = serializeInputBindingsForContract(step.inputs);
            }

            if (step.type === NodeType.DECISION) {
                const rules = (step.branches || [])
                    .map((branch, index) => {
                        const targetId = branch.steps[0]?.id || nextId;
                        if (!targetId) return null;
                        const fallback = index === (step.branches?.length || 1) - 1 ? '1 == 1' : '';
                        return {
                            condition: getBranchCondition(branch) || fallback || '1 == 1',
                            next_node_id: targetId,
                        };
                    })
                    .filter(Boolean);
                config.rules = rules;
            }

            if (step.type === NodeType.LOOP) {
                const childFirst = step.loopSteps?.[0]?.id;
                if (childFirst) {
                    config.child_node_id = childFirst;
                } else {
                    delete config.child_node_id;
                }
            }

            nodes.push({
                id: step.id,
                type: step.type,
                label: step.label,
                config,
            });
            addedNodes.add(step.id);
        }

        if (step.type === NodeType.DECISION) {
            (step.branches || []).forEach((branch) => {
                if (branch.steps.length > 0) {
                    addEdge(step.id, branch.steps[0].id, branch.label);
                    visitList(branch.steps, nextId);
                } else if (nextId) {
                    addEdge(step.id, nextId, branch.label);
                }
            });
            return;
        }

        if (step.type === NodeType.LOOP) {
            if (step.loopSteps && step.loopSteps.length > 0) {
                visitList(step.loopSteps, null);
            }
            if (nextId) {
                addEdge(step.id, nextId);
            }
            return;
        }

        if (step.type !== NodeType.END && nextId) {
            addEdge(step.id, nextId);
        }
    };

    const visitList = (list: StepNode[], continuationId: string | null) => {
        list.forEach((step, index) => {
            const nextId = index < list.length - 1 ? list[index + 1].id : continuationId;
            visitStep(step, nextId);
        });
    };

    visitList(steps, null);

    return { nodes, edges };
}

export function findStepById(steps: StepNode[], stepId: string): StepNode | null {
    for (const step of steps) {
        if (step.id === stepId) return step;
        if (step.branches) {
            for (const branch of step.branches) {
                const found = findStepById(branch.steps, stepId);
                if (found) return found;
            }
        }
        if (step.loopSteps) {
            const found = findStepById(step.loopSteps, stepId);
            if (found) return found;
        }
    }
    return null;
}

export function hasStepId(steps: StepNode[], stepId: string): boolean {
    return Boolean(findStepById(steps, stepId));
}

export function updateStepById(steps: StepNode[], stepId: string, updater: (step: StepNode) => StepNode): StepNode[] {
    let changed = false;

    const next = steps.map((step) => {
        if (step.id === stepId) {
            changed = true;
            return updater(step);
        }

        let nextStep = step;

        if (step.branches) {
            let branchChanged = false;
            const nextBranches = step.branches.map((branch) => {
                const nextBranchSteps = updateStepById(branch.steps, stepId, updater);
                if (nextBranchSteps !== branch.steps) {
                    branchChanged = true;
                    return { ...branch, steps: nextBranchSteps };
                }
                return branch;
            });

            if (branchChanged) {
                nextStep = { ...nextStep, branches: nextBranches };
                changed = true;
            }
        }

        if (step.loopSteps) {
            const nextLoopSteps = updateStepById(step.loopSteps, stepId, updater);
            if (nextLoopSteps !== step.loopSteps) {
                nextStep = { ...nextStep, loopSteps: nextLoopSteps };
                changed = true;
            }
        }

        return nextStep;
    });

    return changed ? next : steps;
}

export function moveStepById(steps: StepNode[], stepId: string, direction: 'up' | 'down'): StepNode[] {
    const index = steps.findIndex((step) => step.id === stepId);
    const targetIndex = direction === 'up' ? index - 1 : index + 1;

    if (index >= 0) {
        if (targetIndex < 0 || targetIndex >= steps.length) return steps;
        const next = [...steps];
        [next[index], next[targetIndex]] = [next[targetIndex], next[index]];
        return next;
    }

    let changed = false;
    const next = steps.map((step) => {
        let nextStep = step;

        if (step.branches) {
            const nextBranches = step.branches.map((branch) => {
                const nextBranchSteps = moveStepById(branch.steps, stepId, direction);
                if (nextBranchSteps !== branch.steps) {
                    changed = true;
                    return { ...branch, steps: nextBranchSteps };
                }
                return branch;
            });
            if (changed) nextStep = { ...nextStep, branches: nextBranches };
        }

        if (!changed && step.loopSteps) {
            const nextLoopSteps = moveStepById(step.loopSteps, stepId, direction);
            if (nextLoopSteps !== step.loopSteps) {
                changed = true;
                nextStep = { ...nextStep, loopSteps: nextLoopSteps };
            }
        }

        return nextStep;
    });

    return changed ? next : steps;
}

export function insertStepAfterId(steps: StepNode[], stepId: string, nextStepToInsert: StepNode): StepNode[] {
    const index = steps.findIndex((step) => step.id === stepId);
    if (index >= 0) {
        return [...steps.slice(0, index + 1), nextStepToInsert, ...steps.slice(index + 1)];
    }

    let changed = false;
    const next = steps.map((step) => {
        let nextStep = step;

        if (step.branches) {
            const nextBranches = step.branches.map((branch) => {
                const nextBranchSteps = insertStepAfterId(branch.steps, stepId, nextStepToInsert);
                if (nextBranchSteps !== branch.steps) {
                    changed = true;
                    return { ...branch, steps: nextBranchSteps };
                }
                return branch;
            });
            if (changed) nextStep = { ...nextStep, branches: nextBranches };
        }

        if (!changed && step.loopSteps) {
            const nextLoopSteps = insertStepAfterId(step.loopSteps, stepId, nextStepToInsert);
            if (nextLoopSteps !== step.loopSteps) {
                changed = true;
                nextStep = { ...nextStep, loopSteps: nextLoopSteps };
            }
        }

        return nextStep;
    });

    return changed ? next : steps;
}

export function addStepToBranchById(steps: StepNode[], decisionId: string, branchId: string, nextStepToInsert: StepNode): StepNode[] {
    return steps.map((step) => {
        if (step.id === decisionId) {
            return {
                ...step,
                branches: (step.branches || []).map((branch) =>
                    branch.id === branchId
                        ? { ...branch, steps: [...branch.steps, nextStepToInsert] }
                        : branch
                ),
            };
        }

        return {
            ...step,
            branches: step.branches?.map((branch) => ({
                ...branch,
                steps: addStepToBranchById(branch.steps, decisionId, branchId, nextStepToInsert),
            })),
            loopSteps: step.loopSteps ? addStepToBranchById(step.loopSteps, decisionId, branchId, nextStepToInsert) : undefined,
        };
    });
}

export function ensureDecisionBranchesById(steps: StepNode[], decisionId: string): StepNode[] {
    return updateStepById(steps, decisionId, (step) => ({
        ...step,
        branches: (step.branches || []).length > 0 ? step.branches : createDefaultDecisionBranches(step.id),
    }));
}

export function removeStepById(steps: StepNode[], stepId: string): StepNode[] {
    return steps
        .filter((step) => step.id !== stepId)
        .map((step) => ({
            ...step,
            branches: step.branches?.map((branch) => ({
                ...branch,
                steps: removeStepById(branch.steps, stepId),
            })),
            loopSteps: step.loopSteps ? removeStepById(step.loopSteps, stepId) : undefined,
        }));
}

export function isStartReady(start: FlowStart): boolean {
    const config = (start.config || {}) as Record<string, unknown>;
    if (start.type === 'EVENT') {
        return Boolean(config.connector_id && config.connector_trigger_id);
    }
    if (start.type === 'DATASTORE_EVENT') {
        return Boolean(config.table_name);
    }
    return true;
}

export function createStart(type: FlowStartType, config: Record<string, unknown> = {}): FlowStart {
    if (type === 'SCHEDULED') {
        return {
            type,
            config: {
                schedule_type: config.schedule_type === ScheduledWorkflowStartConfigType.ONCE
                    ? ScheduledWorkflowStartConfigType.ONCE
                    : ScheduledWorkflowStartConfigType.CRON,
            },
        };
    }

    if (type === 'EVENT') {
        return {
            type,
            config: {
                connector_id: String(config.connector_id || ''),
                connector_trigger_id: String(config.connector_trigger_id || ''),
                trigger_config: typeof config.trigger_config === 'object' && config.trigger_config
                    ? config.trigger_config
                    : {},
            },
        };
    }

    if (type === 'DATASTORE_EVENT') {
        const operations = Array.isArray(config.operations) && config.operations.length > 0
            ? config.operations
            : ['INSERT'];
        return {
            type,
            config: {
                table_name: String(config.table_name || config.name || ''),
                operations,
            },
        };
    }

    return { type: 'MANUAL', config: null };
}
