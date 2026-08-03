'use client';

import { useCallback, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useFlowSession } from 'lemma-sdk/react';
import { useFlow, useFlowRun, useInfiniteFlowRuns } from '@/lib/hooks/use-flows';
import { useFlowRunStream } from '@/lib/hooks/use-flow-run-stream';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { Play, RefreshCw } from '@/components/ui/icons';
import { WorkflowRun } from '@/lib/types';
import {
    getRunCurrentNodeId,
    getRunSortTime,
    pickFreshestRun,
    toRunStatus,
    type RunCardRun,
} from './run-format';
import { ResourceDetailShell, ResourceDetailViewport } from '@/components/pod/resource-layout';
import { RunListCard } from './run-cards';
import { RunErrorBanner, RunHeader, RunStanding, RunTree } from './run-detail';
import { ListSkeleton } from '@/components/shared/loading';
import { showResourceErrorToast } from '@/components/shared/resource-feedback';
import { StepLoader } from '@/components/brand/loader';

interface FlowExecutionPanelProps {
    podId: string;
    flowName: string;
    /**
     * The dock splits the same data in two: `run` is the verb and what happened
     * last, `history` is the list.
     */
    view?: 'run' | 'history';
}

export function FlowExecutionPanel({ podId, flowName, view = 'run' }: FlowExecutionPanelProps) {
    const [isStartingRun, setIsStartingRun] = useState(false);
    const router = useRouter();
    const client = useMemo(() => getLemmaClient(podId), [podId]);
    const { data: flowData } = useFlow(podId, flowName);
    const nodes = flowData?.nodes || [];

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
            // The backend's reason is the whole point here — an empty graph, a
            // permission, a validation failure all come back with readable text.
            // This used to go to the console, so the button just stopped spinning.
            showResourceErrorToast(error, 'Could not start this workflow');
        } finally {
            setIsStartingRun(false);
        }
    };

    const runCount = runs?.length ?? 0;
    const openRun = (runId: string) => {
        router.push(`/pod/${podId}/flows/${encodeURIComponent(flowData?.name || flowName)}/runs/${encodeURIComponent(runId)}`);
    };

    // The dock's "Run" half: the verb, and the last thing that happened. The map
    // and the step count live on the document beside it, so neither is repeated.
    if (view === 'run') {
        const latest = runs[0];
        const latestId = typeof latest?.id === 'string' ? latest.id : '';

        return (
            <div className="flow-dock-run">
                <Button variant="primary"
                    type="button"
                    className="w-full"
                    onClick={() => void handleRun()}
                    disabled={isStartingRun || nodes.length === 0}
                >
                    {isStartingRun ? <StepLoader size="sm" /> : <Play className="h-4 w-4" />}
                    Run now
                </Button>

                {nodes.length === 0 ? (
                    <p className="flow-dock-note">Add a step before there is anything to run.</p>
                ) : latest ? (
                    <div className="flow-dock-latest">
                        <p className="flow-dock-note">Most recent</p>
                        <RunListCard run={latest} nodes={nodes} compact onOpen={() => latestId && openRun(latestId)} />
                    </div>
                ) : (
                    <p className="flow-dock-note">
                        Start it once to inspect each step, output, wait, and agent handoff.
                    </p>
                )}
            </div>
        );
    }

    return (
        <div className="h-full overflow-y-auto bg-transparent" onScroll={handleRunsScroll}>
            <div className="flow-dock-history-bar">
                <span>{runCount} run{runCount === 1 ? '' : 's'} · newest first</span>
                <Button
                    variant="quiet"
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => void refreshRuns()}
                    disabled={isLoadingRuns}
                    aria-label="Refresh runs"
                >
                    <RefreshCw className={cn('h-3.5 w-3.5', isLoadingRuns && 'lemma-spin')} />
                </Button>
            </div>

            {isLoadingRuns ? (
                <ListSkeleton rows={5} className="px-3" />
            ) : (runs || []).length === 0 ? (
                // Just the fact. The verb lives on the Run tab and the steps are
                // on the document, so neither belongs here as well.
                <p className="flow-dock-note px-3">Nothing has run yet.</p>
            ) : (
                <div className="flow-dock-history-list">
                    {(runs || []).map((run) => {
                        const runIdValue = typeof run.id === 'string' ? run.id : '';

                        return (
                            <RunListCard
                                key={runIdValue || `${run.status}-${run.created_at || 'unknown'}`}
                                run={run}
                                nodes={nodes}
                                compact
                                onOpen={() => runIdValue && openRun(runIdValue)}
                            />
                        );
                    })}

                    {(hasNextPage || isFetchingNextPage) ? (
                        <div className="flex justify-center py-4">
                            <Button
                                type="button"
                                variant="quiet"
                                size="sm"
                                className="h-8 gap-2 text-xs"
                                disabled={isFetchingNextPage}
                                onClick={() => void fetchNextPage()}
                            >
                                {isFetchingNextPage ? <StepLoader size="xs" /> : null}
                                {isFetchingNextPage ? 'Loading more' : 'Load more'}
                            </Button>
                        </div>
                    ) : null}
                </div>
            )}
        </div>
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
    const {
        data: runData,
        isLoading: isLoadingRun,
        isFetching: isFetchingRun,
        refetch: refetchRun,
    } = useFlowRun(podId, flowName, runId, { poll: true });
    // Live updates when the stream connects; the poll above stays as the
    // fallback rather than being switched off behind it.
    useFlowRunStream(podId, flowName, runId);
    // A form submission answers with the post-resume run, which is fresher than
    // anything the next poll will carry — hold it until the query catches up.
    const [liveRun, setLiveRun] = useState<RunCardRun | null>(null);
    const scopedLiveRun = liveRun?.id === runId ? liveRun : null;
    const run = useMemo(() => pickFreshestRun(scopedLiveRun, runData), [scopedLiveRun, runData]);

    const nodes = useMemo(() => flowData?.nodes || [], [flowData]);
    const runStatus = toRunStatus(run?.status);
    const currentNodeId = run ? getRunCurrentNodeId(run) : null;
    // The progress hairline went with the bespoke header — the shell's bar is
    // not ours to draw a line under. A step count says more in less space.
    const stepsDone = new Set(
        (run?.step_history || [])
            .filter((step) => String((step as Record<string, unknown>)?.status || '').toUpperCase() === 'COMPLETED')
            .map((step) => String((step as Record<string, unknown>)?.node_id || ''))
    ).size;

    const refresh = useCallback(() => {
        void refetchRun();
    }, [refetchRun]);

    const submitInput = useCallback(async (nodeId: string, data: Record<string, unknown>) => {
        const response = await getLemmaClient(podId).workflows.runs.submitForm(runId, {
            node_id: nodeId,
            inputs: data,
        }, podId);
        setLiveRun(response as unknown as RunCardRun);
        await refetchRun();
    }, [podId, refetchRun, runId]);

    return (
        <ResourceDetailShell>
            <RunHeader
                podId={podId}
                flowName={flowData?.name || flowName}
                runId={runId}
                run={run}
                runStatus={runStatus}
                stepsDone={stepsDone}
                stepsTotal={nodes.length}
                isRefreshing={isFetchingRun}
                onRefresh={refresh}
            />

            <ResourceDetailViewport>
                {isLoadingFlow || isLoadingRun || !run ? (
                    <div className="h-full p-4">
                        <ListSkeleton rows={6} />
                    </div>
                ) : (
                    <div className="resource-page-scroll h-full overflow-y-auto px-4 py-4">
                      <div className="resource-page-column">
                        {/* Where this stands, first — who has it, or what came
                            out. The steps below are the detail behind it. */}
                        <RunErrorBanner
                            error={(run as WorkflowRun).error}
                            failedNodeId={(run as WorkflowRun).failed_node_id}
                            nodes={nodes}
                            className="mb-4"
                        />
                        <RunStanding
                            run={run}
                            runStatus={runStatus}
                            nodes={nodes}
                            onSubmitInput={submitInput}
                        />
                        <RunTree
                            podId={podId}
                            run={run}
                            runStatus={runStatus}
                            nodes={nodes}
                            edges={flowData?.edges || []}
                            currentNodeId={currentNodeId}
                            onRunRefresh={refresh}
                            onSubmitInput={submitInput}
                        />
                      </div>
                    </div>
                )}
            </ResourceDetailViewport>
        </ResourceDetailShell>
    );
}
