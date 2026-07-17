'use client';

import { useCallback, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useFlowSession } from 'lemma-sdk/react';
import { useFlow, useFlowRun, useInfiniteFlowRuns } from '@/lib/hooks/use-flows';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { ResourceMetric, ResourceMetricStrip } from '@/components/pod/resource-layout';
import { cn } from '@/lib/utils';
import {
    ArrowLeft,
    Loader2,
    Maximize2,
    Minimize2,
    Play,
    RefreshCw,
} from '@/components/ui/icons';
import { WorkflowNode, WorkflowRun } from '@/lib/types';
import {
    formatRunIdShort,
    formatStatusLabel,
    formatTimestamp,
    getDisplayNodeLabel,
    getGraphShapeLabel,
    getLatestStepByNode,
    getNodeIconElement,
    getNodeIndex,
    getNodeLabel,
    getNodeTypeLabel,
    getProcedureStatusForVariant,
    getProcedureStepState,
    getProcedureStepStatusLabel,
    getRunCurrentNodeId,
    getRunDisplayDate,
    getRunLastActivityDate,
    getRunProgress,
    getRunSortTime,
    getRunTraceEntries,
    getSelectedPlaybackNodeId,
    getStatusIcon,
    getStatusVariant,
    getStepNarrative,
    getStepPositionLabel,
    getStepSelectionMode,
    pickFreshestRun,
    toRunStatus,
    UNREACHED_NODE_SELECTION_PREFIX,
    type RunCardRun,
    type StepSelectionMode,
    type WorkflowEdgeLike,
} from './run-format';
import { EmptyRunState, RunListCard } from './run-cards';
import { RunPlayback, RunStepRail } from './run-detail';

interface FlowExecutionPanelProps {
    podId: string;
    flowName: string;
}

export function FlowExecutionPanel({ podId, flowName }: FlowExecutionPanelProps) {
    const [isStartingRun, setIsStartingRun] = useState(false);
    const router = useRouter();
    const client = useMemo(() => getLemmaClient(podId), [podId]);
    const { data: flowData } = useFlow(podId, flowName);
    const nodes = flowData?.nodes || [];
    const edges = (flowData?.edges || []) as WorkflowEdgeLike[];

    const flowSession = useFlowSession({
        client,
        podId,
        flowName,
        runId: null,
        autoPoll: false,
        pollIntervalMs: 2000,
    });

    const { start: startFlowRun } = flowSession;
    const {
        data: runPages,
        isLoading: isLoadingRuns,
        isFetchingNextPage,
        hasNextPage,
        fetchNextPage,
        refetch: refetchRuns,
    } = useInfiniteFlowRuns(podId, flowName, 10, { pollWhenLive: true });
    const rawRuns = useMemo(() => {
        const seen = new Set<string>();
        const flattened: WorkflowRun[] = [];

        for (const page of runPages?.pages || []) {
            for (const run of page.items) {
                if (seen.has(run.id)) continue;
                seen.add(run.id);
                flattened.push(run);
            }
        }

        return flattened;
    }, [runPages]);
    const runs = useMemo(() => {
        return [...(rawRuns || [])].sort((a, b) => {
            return getRunSortTime(b) - getRunSortTime(a);
        });
    }, [rawRuns]);
    const refreshRuns = useCallback(async () => {
        await refetchRuns();
    }, [refetchRuns]);
    const handleRunsScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
        if (!hasNextPage || isFetchingNextPage) return;

        const element = event.currentTarget;
        const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
        if (distanceFromBottom < 240) {
            void fetchNextPage();
        }
    }, [fetchNextPage, hasNextPage, isFetchingNextPage]);

    const handleRun = async () => {
        setIsStartingRun(true);
        try {
            const result = await startFlowRun({ flowName });
            if (result.id) {
                router.push(`/pod/${podId}/flows/${encodeURIComponent(flowData?.name || flowName)}/runs/${encodeURIComponent(result.id)}`);
                return;
            }
            await refreshRuns();
        } catch (error) {
            console.error('Failed to run automation:', error);
        } finally {
            setIsStartingRun(false);
        }
    };

    const runCount = runs?.length ?? 0;

    return (
        <div className="context-shell h-full overflow-y-auto bg-transparent" onScroll={handleRunsScroll}>
            <div className="context-toolbar mb-4 flex-col items-stretch sm:flex-row sm:items-center">
                <ResourceMetricStrip className="mb-0">
                    <ResourceMetric label="Runs" value={runCount} active />
                    {nodes.length > 0 ? <ResourceMetric label="Steps" value={nodes.length} /> : null}
                </ResourceMetricStrip>
                <div className="flex items-center justify-between gap-2 sm:justify-end">
                    <p className="min-w-0 flex-1 truncate text-xs text-[var(--text-tertiary)] sm:hidden">
                        Newest first.
                    </p>
                    <div className="flex items-center gap-2">
                        <Button
                            variant="outline"
                            size="icon"
                            className="h-8 w-8"
                            onClick={() => void refreshRuns()}
                            disabled={isLoadingRuns}
                            aria-label="Refresh runs"
                        >
                            <RefreshCw className={cn('h-4 w-4', isLoadingRuns && 'animate-spin')} />
                        </Button>
                        <Button size="sm" className="h-8 gap-2 px-3 text-xs" onClick={() => void handleRun()} disabled={isStartingRun}>
                            {isStartingRun ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                            Run now
                        </Button>
                    </div>
                </div>
            </div>

            {isLoadingRuns ? (
                <div className="flex items-center justify-center px-4 py-16">
                    <Loader2 className="h-5 w-5 animate-spin text-[var(--text-tertiary)]" />
                </div>
            ) : (runs || []).length === 0 ? (
                <EmptyRunState
                    nodes={nodes}
                    edges={edges}
                    onRun={handleRun}
                    isStartingRun={isStartingRun}
                    editHref={`/pod/${podId}/flows/${encodeURIComponent(flowName)}?mode=edit`}
                />
            ) : (
                <div className="lemma-index-list lemma-run-list">
                    {(runs || []).map((run) => {
                        const runIdValue = typeof run.id === 'string' ? run.id : '';

                        return (
                            <RunListCard
                                key={runIdValue || `${run.status}-${run.created_at || 'unknown'}`}
                                run={run}
                                nodes={nodes}
                                onOpen={() => {
                                    if (!runIdValue) return;
                                    router.push(`/pod/${podId}/flows/${encodeURIComponent(flowData?.name || flowName)}/runs/${encodeURIComponent(runIdValue)}`);
                                }}
                            />
                        );
                    })}

                    {(hasNextPage || isFetchingNextPage) ? (
                        <div className="flex justify-center py-4">
                            <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                className="h-8 gap-2 text-xs"
                                disabled={isFetchingNextPage}
                                onClick={() => void fetchNextPage()}
                            >
                                {isFetchingNextPage ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                                {isFetchingNextPage ? 'Loading more' : 'Load more'}
                            </Button>
                        </div>
                    ) : null}
                </div>
            )}
        </div>
    );
}

export function RunExperienceDialog({
    open,
    onOpenChange,
    isMaximized,
    onToggleMaximized,
    onOpenFullPage,
    podId,
    flowName,
    run,
    nodes,
    onRunRefresh,
    onSubmitInput,
}: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    isMaximized: boolean;
    onToggleMaximized: () => void;
    onOpenFullPage?: () => void;
    podId: string;
    flowName: string;
    run: RunCardRun | null;
    nodes: WorkflowNode[];
    onRunRefresh?: () => Promise<void> | void;
    onSubmitInput: (nodeId: string, data: Record<string, unknown>) => Promise<void>;
}) {
    const [selectedStepSelection, setSelectedStepSelection] = useState<{
        runId: string | null;
        nodeId: string;
        mode: StepSelectionMode;
    } | null>(null);
    const runStatus = toRunStatus(run?.status);
    const currentNodeId = run ? getRunCurrentNodeId(run) : null;
    const currentNodePosition = getStepPositionLabel(currentNodeId, nodes);
    const runDisplayDate = run ? getRunDisplayDate(run) : null;
    const lastActivityAt = getRunLastActivityDate(run || undefined);
    const runIdValue = typeof run?.id === 'string' ? run.id : null;
    const runProgress = run ? getRunProgress(run, nodes, currentNodeId, runStatus) : 0;
    const selectedStepNodeId = selectedStepSelection?.runId === runIdValue
        ? getSelectedPlaybackNodeId(selectedStepSelection, nodes, currentNodeId)
        : null;
    const selectStepNode = (nodeId: string) => {
        setSelectedStepSelection({
            runId: runIdValue,
            nodeId,
            mode: getStepSelectionMode({
                nodeId,
                run,
                nodes,
                currentNodeId,
                runStatus,
            }),
        });
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent
                className={cn(
                    'flex flex-col gap-0 overflow-hidden p-0',
                    isMaximized
                        ? 'h-[calc(100vh-2rem)] max-h-[calc(100vh-2rem)] w-[calc(100vw-2rem)] max-w-none'
                        : 'h-[82vh] max-h-[82vh] w-[min(1180px,calc(100vw-3rem))] max-w-none'
                )}
            >
                <div className="relative bg-[var(--card-bg)] px-4 py-2.5">
                    <div className="grid min-w-0 gap-2 pr-10 lg:grid-cols-[minmax(13rem,0.8fr)_minmax(18rem,1.1fr)_auto] lg:items-center">
                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                            <DialogTitle className="mr-1 max-w-[16rem] truncate text-sm font-semibold text-[var(--text-primary)]">
                                {flowName} run
                            </DialogTitle>
                            <span className="font-mono text-xs font-medium text-[var(--text-tertiary)]">#{formatRunIdShort(runIdValue)}</span>
                            <Badge variant={getStatusVariant(runStatus)} className="flow-execution-badge">
                                {getStatusIcon(runStatus)}
                                {formatStatusLabel(runStatus)}
                            </Badge>
                            <Badge variant="default" className="flow-execution-badge-meta">{currentNodePosition}</Badge>
                        </div>
                        {run ? (
                            <RunStepRail
                                run={run}
                                nodes={nodes}
                                runStatus={runStatus}
                                currentNodeId={currentNodeId}
                                selectedNodeId={selectedStepNodeId}
                                onSelectNode={selectStepNode}
                                className="hidden lg:flex"
                            />
                        ) : null}
                        <div className="mr-6 flex items-center justify-end gap-2">
                            <div className="hidden items-center gap-2 whitespace-nowrap text-xs text-[var(--text-secondary)] xl:flex">
                                <span>Started {formatTimestamp(runDisplayDate)}</span>
                                <span className="text-[var(--text-tertiary)]">·</span>
                                <span>Updated {formatTimestamp(lastActivityAt)}</span>
                            </div>
                            <Button
                                variant="outline"
                                size="icon"
                                className="h-8 w-8 shrink-0"
                                onClick={onOpenFullPage ?? onToggleMaximized}
                                aria-label={onOpenFullPage ? 'Open run page' : isMaximized ? 'Restore run view' : 'Maximize run view'}
                            >
                                {onOpenFullPage ? <Maximize2 className="h-4 w-4" /> : isMaximized ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
                            </Button>
                        </div>
                    </div>
                    {run ? (
                        <RunStepRail
                            run={run}
                            nodes={nodes}
                            runStatus={runStatus}
                            currentNodeId={currentNodeId}
                            selectedNodeId={selectedStepNodeId}
                            onSelectNode={selectStepNode}
                            className="mt-2 lg:hidden"
                        />
                    ) : null}
                    <div className="absolute inset-x-0 bottom-0 h-px bg-[color:color-mix(in_srgb,var(--border-subtle)_44%,transparent)]">
                        {/* eslint-disable-next-line no-restricted-syntax -- Runtime progress scale is data-driven geometry. */}
                        <div className="h-px w-full origin-left bg-[var(--progress-segment-bg)] transition-transform duration-300" style={{ transform: `scaleX(${runProgress / 100})` }} />
                    </div>
                </div>

                <div className="min-h-0 flex-1 overflow-hidden bg-[var(--bg-canvas)]">
                    {!run ? (
                        <div className="flex h-full items-center justify-center">
                            <Loader2 className="h-5 w-5 animate-spin text-[var(--text-tertiary)]" />
                        </div>
                    ) : (
                        <RunPlayback
                            podId={podId}
                            run={run}
                            runStatus={runStatus}
                            nodes={nodes}
                            currentNodeId={currentNodeId}
                            selectedNodeId={selectedStepNodeId}
                            onRunRefresh={onRunRefresh}
                            onSubmitInput={onSubmitInput}
                        />
                    )}
                </div>
            </DialogContent>
        </Dialog>
    );
}

export function FlowRunPageSurface({
    podId,
    flowName,
    runId,
}: {
    podId: string;
    flowName: string;
    runId: string;
}) {
    const { data: flowData, isLoading: isLoadingFlow } = useFlow(podId, flowName);
    const { data: runData, isLoading: isLoadingRun, refetch: refetchRun } = useFlowRun(podId, flowName, runId, { poll: true });
    const [liveRun, setLiveRun] = useState<RunCardRun | null>(null);
    const [selectedTraceSelection, setSelectedTraceSelection] = useState<{ runId: string; entryKey: string } | null>(null);
    const selectedEntryKey = selectedTraceSelection?.runId === runId ? selectedTraceSelection.entryKey : null;
    const scopedLiveRun = liveRun?.id === runId ? liveRun : null;
    const run = useMemo(() => pickFreshestRun(scopedLiveRun, runData), [scopedLiveRun, runData]);
    const nodes = flowData?.nodes || [];
    const edges = (flowData?.edges || []) as WorkflowEdgeLike[];
    const runStatus = toRunStatus(run?.status);
    const currentNodeId = run ? getRunCurrentNodeId(run) : null;
    const currentNodePosition = getStepPositionLabel(currentNodeId, nodes);
    const runDisplayDate = run ? getRunDisplayDate(run) : null;
    const lastActivityAt = getRunLastActivityDate(run || undefined);
    const progress = run ? getRunProgress(run, nodes, currentNodeId, runStatus) : 0;
    const currentNodeLabel = getNodeLabel(currentNodeId, nodes);
    const graphShape = getGraphShapeLabel(nodes, edges);
    const latestStepByNode = useMemo(() => getLatestStepByNode(run?.step_history || []), [run?.step_history]);
    const currentNodeIndex = getNodeIndex(currentNodeId, nodes);
    const traceEntries = run ? getRunTraceEntries(run, nodes, currentNodeId, runStatus) : [];
    const selectedUnreachedNodeId = selectedEntryKey?.startsWith(UNREACHED_NODE_SELECTION_PREFIX)
        ? selectedEntryKey.slice(UNREACHED_NODE_SELECTION_PREFIX.length)
        : null;
    const selectedTraceEntry = selectedEntryKey
        && !selectedUnreachedNodeId
        ? traceEntries.find((entry) => entry.key === selectedEntryKey) || null
        : null;
    const fallbackTraceEntry = currentNodeId
        ? [...traceEntries].reverse().find((entry) => entry.nodeId === currentNodeId) || null
        : traceEntries[traceEntries.length - 1] || null;
    const headerTraceEntry = selectedTraceEntry || fallbackTraceEntry;
    const inspectedNode = selectedUnreachedNodeId
        ? nodes.find((node) => node.id === selectedUnreachedNodeId) || null
        : headerTraceEntry?.node || null;
    const currentNode = currentNodeId ? nodes.find((node) => node.id === currentNodeId) || null : null;
    const headerNode = inspectedNode || currentNode;
    const headerNodePosition = selectedUnreachedNodeId
        ? 'Not reached'
        : headerTraceEntry
            ? `Event ${headerTraceEntry.index + 1} of ${traceEntries.length}`
            : currentNodePosition;
    const headerNodeLabel = headerNode ? getDisplayNodeLabel(headerNode) : currentNodeLabel;
    const headerNodeIndex = getNodeIndex(headerNode?.id, nodes);
    const headerNextNode = headerTraceEntry ? traceEntries[headerTraceEntry.index + 1]?.node || null : null;
    const headerStep = headerTraceEntry?.step || (headerNode ? latestStepByNode.get(headerNode.id) || null : null);
    const headerStepState = selectedUnreachedNodeId ? 'pending' : headerTraceEntry?.state || (headerNode
        ? getProcedureStepState({
            node: headerNode,
            step: headerStep,
            index: headerNodeIndex,
            currentNodeId: currentNodeId ?? undefined,
            currentNodeIndex,
            runStatus,
        })
        : 'pending');

    return (
        <div className="flex h-full min-h-0 flex-col bg-[var(--bg-canvas)]">
            <header className="relative flex min-h-14 shrink-0 items-center border-b border-[color:color-mix(in_srgb,var(--border-subtle)_52%,transparent)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_88%,transparent)] px-4 py-2 backdrop-blur-sm">
                <div className="flex w-full min-w-0 flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div className="flex min-w-0 items-center gap-2.5 md:flex-1">
                        <Link
                            href={`/pod/${podId}/flows/${encodeURIComponent(flowName)}`}
                            className="lemma-card-icon-control custom-focus-ring h-8 w-8 shrink-0"
                            aria-label="Back to workflow runs"
                        >
                            <ArrowLeft className="h-4 w-4" />
                        </Link>
                        {headerNode ? (
                            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)] text-[var(--text-secondary)]">
                                {getNodeIconElement(headerNode.type)}
                            </span>
                        ) : null}
                        <div className="min-w-0 flex-1">
                            <div className="flex min-w-0 items-center gap-2">
                                <h1 className="truncate text-sm font-semibold tracking-normal text-[var(--text-primary)]">{headerNodeLabel || flowData?.name || flowName}</h1>
                                {headerNode ? (
                                    <>
                                        <span className="chip chip-pill chip-sm chip-muted type-micro-label">{getNodeTypeLabel(headerNode.type)}</span>
                                        <Badge variant={getStatusVariant(getProcedureStatusForVariant(headerStepState))} className="flow-execution-badge-compact">
                                            {getProcedureStepStatusLabel(headerStepState)}
                                        </Badge>
                                    </>
                                ) : null}
                            </div>
                            {run ? (
                                <div className="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-[var(--text-tertiary)]">
                                    <span>{headerNodePosition}</span>
                                    {headerNode ? (
                                        <>
                                            <span>·</span>
                                            <span className="truncate">{getStepNarrative({
                                                node: headerNode,
                                                state: headerStepState,
                                                nextNodeLabel: headerNextNode ? getDisplayNodeLabel(headerNextNode) : null,
                                                index: headerNodeIndex >= 0 ? headerNodeIndex : 0,
                                                totalSteps: nodes.length,
                                            })}</span>
                                        </>
                                    ) : null}
                                </div>
                            ) : null}
                        </div>
                    </div>
                    {run ? (
                        <div className="hidden min-w-0 flex-1 flex-col items-end text-right md:flex">
                            <div className="flex w-full min-w-0 items-center justify-end gap-2">
                                <span className="truncate text-sm font-medium text-[var(--text-primary)]">{flowData?.name || flowName} run</span>
                                <span className="font-mono text-xs font-medium text-[var(--text-tertiary)]">#{formatRunIdShort(runId)}</span>
                                <Badge variant={getStatusVariant(runStatus)} className="flow-execution-badge-compact">
                                    {getStatusIcon(runStatus)}
                                    {formatStatusLabel(runStatus)}
                                </Badge>
                            </div>
                            <div className="mt-0.5 flex w-full min-w-0 items-center justify-end gap-2 text-xs text-[var(--text-tertiary)]">
                                <span>Started {formatTimestamp(runDisplayDate)}</span>
                                <span>·</span>
                                <span>Updated {formatTimestamp(lastActivityAt)}</span>
                                <span>·</span>
                                <span className="truncate">{graphShape}</span>
                            </div>
                        </div>
                    ) : null}
                </div>
                <div className="absolute inset-x-0 bottom-0 h-px bg-[color:color-mix(in_srgb,var(--border-subtle)_44%,transparent)]">
                    {/* eslint-disable-next-line no-restricted-syntax -- Runtime progress scale is data-driven geometry. */}
                    <div className="h-px w-full origin-left bg-[var(--progress-segment-bg)] transition-transform duration-300" style={{ transform: `scaleX(${progress / 100})` }} />
                </div>
            </header>

            <div className="min-h-0 flex-1 overflow-hidden">
                {isLoadingFlow || isLoadingRun || !run ? (
                    <div className="flex h-full items-center justify-center">
                        <Loader2 className="h-5 w-5 animate-spin text-[var(--text-tertiary)]" />
                    </div>
                ) : (
                    <RunPlayback
                        podId={podId}
                        run={run}
                        runStatus={runStatus}
                        nodes={nodes}
                        edges={edges}
                        currentNodeId={currentNodeId}
                        selectedEntryKey={selectedEntryKey}
                        onSelectedEntryChange={(entryKey) => setSelectedTraceSelection({ runId, entryKey })}
                        onRunRefresh={() => void refetchRun()}
                        onSubmitInput={async (nodeId, data) => {
                            const response = await getLemmaClient(podId).workflows.runs.submitForm(runId, {
                                node_id: nodeId,
                                inputs: data,
                            }, podId);
                            setLiveRun(response as unknown as RunCardRun);
                            await refetchRun();
                        }}
                    />
                )}
            </div>
        </div>
    );
}
