import { useState } from 'react';
import { Clock } from '@/components/ui/icons';
import { QuietEmptyState } from '@/components/shared/empty-state';
import { cn } from '@/lib/utils';
import { NodeType, WorkflowNode } from '@/lib/types';
import {
    COMPLETE_STATUSES,
    UNREACHED_NODE_SELECTION_PREFIX,
    getDisplayNodeLabel,
    getLatestStepByNode,
    getNextGraphNode,
    getNodeIndex,
    getProcedureStepState,
    getProcedureStepStatusLabel,
    getRunTraceEntries,
    type RunCardRun,
    type RunTraceEntry,
    type WorkflowEdgeLike,
} from '../run-format';
import { InlineStepDot } from './step-dots';
import { RunPlaybackStep } from './run-steps';

export function RunPlayback({
    podId,
    run,
    runStatus,
    nodes,
    edges = [],
    currentNodeId,
    selectedNodeId: externalSelectedNodeId,
    selectedEntryKey: externalSelectedEntryKey,
    onSelectedEntryChange,
    onRunRefresh,
    onSubmitInput,
}: {
    podId: string;
    run: RunCardRun;
    runStatus: string;
    nodes: WorkflowNode[];
    edges?: WorkflowEdgeLike[];
    currentNodeId: string | null;
    selectedNodeId?: string | null;
    selectedEntryKey?: string | null;
    onSelectedEntryChange?: (entryKey: string) => void;
    onRunRefresh?: () => Promise<void> | void;
    onSubmitInput: (nodeId: string, data: Record<string, unknown>) => Promise<void>;
}) {
    const selectedNodeId = externalSelectedNodeId || null;
    const [internalSelectedEntryKey, setInternalSelectedEntryKey] = useState<string | null>(externalSelectedEntryKey || null);
    const selectedEntryKey = typeof externalSelectedEntryKey === 'undefined' ? internalSelectedEntryKey : externalSelectedEntryKey;
    const latestStepByNode = getLatestStepByNode(run.step_history || []);
    const traceEntries = getRunTraceEntries(run, nodes, currentNodeId, runStatus);
    const currentNodeIndex = getNodeIndex(currentNodeId, nodes);
    const selectedUnreachedNodeId = selectedEntryKey?.startsWith(UNREACHED_NODE_SELECTION_PREFIX)
        ? selectedEntryKey.slice(UNREACHED_NODE_SELECTION_PREFIX.length)
        : null;
    const activeEntry = selectedEntryKey
        && !selectedUnreachedNodeId
        ? traceEntries.find((entry) => entry.key === selectedEntryKey) || null
        : selectedNodeId
            ? [...traceEntries].reverse().find((entry) => entry.nodeId === selectedNodeId) || null
            : currentNodeId
                ? [...traceEntries].reverse().find((entry) => entry.nodeId === currentNodeId) || null
                : traceEntries[traceEntries.length - 1] || null;
    const fallbackNodeId = selectedUnreachedNodeId || activeEntry?.nodeId || selectedNodeId || currentNodeId || nodes[0]?.id || null;
    const activeIndex = getNodeIndex(fallbackNodeId, nodes);
    const activeNode = activeEntry?.node || (activeIndex >= 0 ? nodes[activeIndex] : nodes[0] || null);
    const activeStep = selectedUnreachedNodeId ? null : activeEntry?.step || (activeNode ? latestStepByNode.get(activeNode.id) || null : null);
    const previousTraceEntry = activeEntry && activeEntry.index > 0 ? traceEntries[activeEntry.index - 1] || null : null;
    const previousStep = previousTraceEntry?.step || null;
    const nextTraceEntry = activeEntry ? traceEntries[activeEntry.index + 1] || null : null;
    const nextNode = nextTraceEntry?.node || getNextGraphNode(activeNode?.id, nodes, edges) || nodes[activeIndex + 1] || null;
    const activeState = selectedUnreachedNodeId ? 'pending' : activeEntry?.state ?? (activeNode
        ? getProcedureStepState({
            node: activeNode,
            step: activeStep,
            index: activeIndex,
            currentNodeId: currentNodeId ?? undefined,
            currentNodeIndex,
            runStatus,
        })
        : 'pending');
    const isAgentActive = activeNode?.type === NodeType.AGENT;
    const handleSelectEntry = (entryKey: string) => {
        setInternalSelectedEntryKey(entryKey);
        onSelectedEntryChange?.(entryKey);
    };

    return (
        <div className="surface-split-2 grid h-full min-h-0 bg-[var(--bg-canvas)] lg:grid-cols-[17rem_minmax(0,1fr)]">
            <RunProgressRail
                nodes={nodes}
                traceEntries={traceEntries}
                currentNodeId={currentNodeId}
                selectedEntryKey={selectedUnreachedNodeId ? `${UNREACHED_NODE_SELECTION_PREFIX}${selectedUnreachedNodeId}` : activeEntry?.key || null}
                onSelectEntry={handleSelectEntry}
            />

            <main
                className={cn(
                    'min-h-0 min-w-0 overflow-y-auto border-t border-[color:color-mix(in_srgb,var(--border-subtle)_44%,transparent)] lg:border-l lg:border-t-0',
                    isAgentActive ? 'p-0' : 'px-6 py-5'
                )}
            >
                <div className={cn('w-full', isAgentActive ? 'h-full min-h-0' : 'max-w-none')}>
                    {activeNode ? (
                        <RunPlaybackStep
                            podId={podId}
                            node={activeNode}
                            step={activeStep}
                            previousStep={previousStep}
                            run={run}
                            index={activeIndex >= 0 ? activeIndex : 0}
                            totalSteps={nodes.length}
                            state={activeState}
                            runStatus={runStatus}
                            nextNodeLabel={nextNode ? getDisplayNodeLabel(nextNode) : null}
                            onRunRefresh={onRunRefresh}
                            onSubmitInput={onSubmitInput}
                            chamber
                        />
                    ) : (
                        <QuietEmptyState icon={<Clock className="h-4 w-4" />}>
                            This run has not recorded an execution path yet.
                        </QuietEmptyState>
                    )}
                </div>
            </main>
        </div>
    );
}

function RunProgressRail({
    nodes,
    traceEntries,
    currentNodeId,
    selectedEntryKey,
    onSelectEntry,
}: {
    nodes: WorkflowNode[];
    traceEntries: RunTraceEntry[];
    currentNodeId: string | null;
    selectedEntryKey: string | null;
    onSelectEntry: (entryKey: string) => void;
}) {
    const reachedNodeIds = new Set(traceEntries.map((entry) => entry.nodeId));
    const unreachedNodes = nodes.filter((node) => !reachedNodeIds.has(node.id));

    return (
        <aside className="min-h-0 overflow-y-auto bg-[var(--card-bg)] px-3 py-4">
            <div className="mb-3 flex items-center justify-between gap-2 px-1">
                <div>
                    <p className="type-eyebrow">Progress</p>
                    <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                        {traceEntries.length} event{traceEntries.length === 1 ? '' : 's'}
                    </p>
                </div>
            </div>

            <div className="space-y-1">
                {traceEntries.length > 0 ? traceEntries.map((traceEntry) => {
                    const selected = selectedEntryKey === traceEntry.key;
                    const current = currentNodeId === traceEntry.nodeId;
                    const state = traceEntry.state;

                    return (
                        <button
                            key={traceEntry.key}
                            type="button"
                            onClick={() => onSelectEntry(traceEntry.key)}
                            className={cn(
                                'flow-execution-trace-button custom-focus-ring flex w-full items-start gap-2 rounded-lg px-2 py-2.5 transition-colors',
                                selected ? 'bg-[var(--surface-2)]' : 'hover:bg-[var(--surface-2)]',
                                !current && state === 'pending' && 'opacity-60'
                            )}
                            aria-current={current ? 'step' : undefined}
                            aria-pressed={selected}
                        >
                            <span className="mt-0.5">
                                <InlineStepDot state={state} active={selected || current} />
                            </span>
                            <span className="min-w-0 flex-1">
                                <span className={cn('block truncate text-sm text-[var(--text-secondary)]', (selected || current) && 'font-medium text-[var(--text-primary)]')}>
                                    {traceEntry.label}
                                </span>
                                <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-[var(--text-tertiary)]">
                                    <span>{getProcedureStepStatusLabel(state)}</span>
                                    {traceEntry.occurrence > 1 ? <span>Run {traceEntry.occurrence}</span> : null}
                                </span>
                            </span>
                        </button>
                    );
                }) : (
                    <QuietEmptyState icon={<Clock className="h-4 w-4" />}>
                        No execution events recorded yet.
                    </QuietEmptyState>
                )}

                {unreachedNodes.length > 0 ? (
                    <div className="pt-3">
                        <div className="mb-1 px-2 type-eyebrow-medium">Not reached</div>
                        <div className="space-y-1">
                            {unreachedNodes.map((node) => {
                                const selectionKey = `${UNREACHED_NODE_SELECTION_PREFIX}${node.id}`;
                                const selected = selectedEntryKey === selectionKey;

                                return (
                                    <button
                                        key={node.id}
                                        type="button"
                                        onClick={() => onSelectEntry(selectionKey)}
                                        className={cn(
                                            'flow-execution-trace-button custom-focus-ring flex w-full items-start gap-2 rounded-lg px-2 py-2.5 text-[var(--text-tertiary)] opacity-70 transition-colors',
                                            selected ? 'bg-[var(--surface-2)] opacity-100' : 'hover:bg-[var(--surface-2)] hover:opacity-100'
                                        )}
                                        aria-pressed={selected}
                                    >
                                        <span className="mt-0.5">
                                            <InlineStepDot state="pending" active={selected} />
                                        </span>
                                        <span className="min-w-0 flex-1">
                                            <span className={cn('block truncate text-sm', selected && 'font-medium text-[var(--text-primary)]')}>
                                                {getDisplayNodeLabel(node)}
                                            </span>
                                            <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs">
                                                <span>Skipped</span>
                                            </span>
                                        </span>
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                ) : null}
            </div>
        </aside>
    );
}

export function RunStepRail({
    run,
    nodes,
    runStatus,
    currentNodeId,
    selectedNodeId,
    onSelectNode,
    className,
}: {
    run: RunCardRun;
    nodes: WorkflowNode[];
    runStatus: string;
    currentNodeId: string | null;
    selectedNodeId?: string | null;
    onSelectNode?: (nodeId: string) => void;
    className?: string;
}) {
    if (nodes.length === 0) return null;

    const latestStepByNode = getLatestStepByNode(run.step_history || []);
    const currentNodeIndex = getNodeIndex(currentNodeId, nodes);
    const selectedNodeIndex = getNodeIndex(selectedNodeId, nodes);
    const activeIndex = selectedNodeIndex >= 0
        ? selectedNodeIndex
        : currentNodeIndex >= 0
        ? currentNodeIndex
        : COMPLETE_STATUSES.has(runStatus)
            ? Math.max(0, nodes.length - 1)
            : 0;

    return (
        <div className={cn('min-w-0 overflow-x-auto', className)}>
            <div className="flex min-w-max items-center gap-5">
                {nodes.map((node, index) => {
                    const step = latestStepByNode.get(node.id) || null;
                    const state = getProcedureStepState({
                        node,
                        step,
                        index,
                        currentNodeId: currentNodeId ?? undefined,
                        currentNodeIndex,
                        runStatus,
                    });
                    const active = index === activeIndex;
                    const current = index === currentNodeIndex;
                    const isStatefulActiveStep = active && (state === 'waiting' || state === 'failed' || state === 'running');

                    return (
                        <button
                            key={node.id || `${node.type}-${index}`}
                            type="button"
                            onClick={() => onSelectNode?.(node.id)}
                            className={cn(
                                'flow-execution-rail-button flex min-w-[6.4rem] items-center gap-2 rounded-md border border-transparent px-1.5 py-1 text-xs transition-colors',
                                !active && 'hover:bg-[var(--surface-2)]',
                                active && !isStatefulActiveStep && 'bg-[var(--surface-2)]',
                                active && state === 'waiting' && 'state-surface-warning hover:brightness-[0.98]',
                                active && state === 'failed' && 'state-surface-error hover:brightness-[0.98]',
                                active && state === 'running' && 'state-surface-running hover:brightness-[0.98]'
                            )}
                            aria-current={current ? 'step' : undefined}
                            aria-pressed={active}
                        >
                            <InlineStepDot state={state} active={active} />
                            <div className="min-w-0">
                                <p className={cn('max-w-[7.25rem] truncate text-[var(--text-tertiary)]', active && 'font-medium text-[var(--text-primary)]')}>
                                    {getDisplayNodeLabel(node)}
                                </p>
                                <p className="mt-0.5 type-eyebrow-medium">
                                    {getProcedureStepStatusLabel(state)}
                                </p>
                            </div>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}
