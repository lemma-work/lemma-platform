import Link from 'next/link';
import { Box, ChevronRight, Loader2, Play, Sparkles } from '@/components/ui/icons';
import { Button } from '@/components/ui/button';
import { EmptyState, QuietEmptyState } from '@/components/shared/empty-state';
import { cn } from '@/lib/utils';
import { WorkflowNode, WorkflowRun } from '@/lib/types';
import {
    COMPLETE_STATUSES,
    FAILURE_STATUSES,
    WAITING_STATUSES,
    formatDuration,
    formatRunIdShort,
    formatTimestamp,
    getDisplayNodeLabel,
    getGraphShapeLabel,
    getNodeIconElement,
    getNodeOutgoingCount,
    getNodeTypeLabel,
    getRunCurrentNodeId,
    getRunDisplayDate,
    getRunHistoryDetail,
    getRunHistorySubdetail,
    getRunHistoryTitle,
    getStepPositionLabel,
    isActiveStepStatus,
    parseApiDate,
    toRunStatus,
    type RunCardRun,
    type WorkflowEdgeLike,
} from './run-format';

export function EmptyRunState({
    nodes,
    edges,
    onRun,
    isStartingRun,
    editHref,
}: {
    nodes: WorkflowNode[];
    edges: WorkflowEdgeLike[];
    onRun: () => Promise<void>;
    isStartingRun: boolean;
    editHref: string;
}) {
    const hasProcedure = nodes.length > 0;
    const graphShape = getGraphShapeLabel(nodes, edges);

    return (
        <div className="px-1 py-4">
            <div className="w-full">
                <EmptyState
                    variant="panel"
                    icon={hasProcedure ? <Play className="h-5 w-5" /> : <Box className="h-5 w-5" />}
                    title={hasProcedure ? 'No workflow runs yet' : 'No steps to run yet'}
                    description={hasProcedure
                        ? 'Start this workflow once to inspect each step, output, wait, and agent handoff.'
                        : 'Add the first workflow step before there is anything useful to run.'}
                    action={hasProcedure ? (
                        <Button size="sm" className="gap-2" onClick={() => void onRun()} disabled={isStartingRun}>
                            {isStartingRun ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                            Run now
                        </Button>
                    ) : (
                        <Link href={editHref}>
                            <Button size="sm" variant="outline" className="gap-2">
                                <Box className="h-4 w-4" />
                                Add steps
                            </Button>
                        </Link>
                    )}
                    className="py-14"
                />

                {nodes.length > 0 ? (
                    <div className="mt-9 max-w-3xl">
                        <div className="mb-3 flex items-center justify-between gap-3">
                            <h4 className="text-sm font-semibold text-[var(--text-primary)]">Workflow map</h4>
                            <span className="text-xs text-[var(--text-tertiary)]">
                                {graphShape}
                            </span>
                        </div>
                        <div className="lemma-index-list">
                            {nodes.slice(0, 6).map((node, index) => (
                                <div key={node.id || `${node.type}-${index}`} className="lemma-index-row flex min-h-14 items-center gap-3 px-3 py-2.5">
                                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)]">
                                        {getNodeIconElement(node.type)}
                                    </span>
                                    <div className="min-w-0 flex-1">
                                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                                            <p className="truncate text-sm font-medium text-[var(--text-primary)]">{getDisplayNodeLabel(node)}</p>
                                            <span className="text-xs text-[var(--text-tertiary)]">{getNodeTypeLabel(node.type)}</span>
                                            {getNodeOutgoingCount(node.id, edges) > 1 ? (
                                                <span className="chip chip-pill chip-sm chip-muted type-micro-label">Branches</span>
                                            ) : null}
                                        </div>
                                    </div>
                                </div>
                            ))}
                            {nodes.length > 6 ? (
                                <div className="lemma-index-row px-3 py-2.5 text-xs text-[var(--text-tertiary)]">
                                    {nodes.length - 6} more step{nodes.length - 6 === 1 ? '' : 's'}
                                </div>
                            ) : null}
                        </div>
                    </div>
                ) : (
                    <QuietEmptyState icon={<Box className="h-4 w-4" />} className="mt-4 justify-center">
                        No procedure yet.
                    </QuietEmptyState>
                )}
            </div>
        </div>
    );
}

export function RunListCard({
    run,
    nodes,
    onOpen,
}: {
    run: RunCardRun;
    nodes: WorkflowNode[];
    onOpen: () => void;
}) {
    const runStatus = toRunStatus(run.status);
    const runIdValue = typeof run.id === 'string' ? run.id : '';
    const runDisplayDate = getRunDisplayDate(run);
    const runCompletedAt = parseApiDate(run.completed_at);
    const runDuration = formatDuration(runDisplayDate, runCompletedAt);
    const currentNodeId = getRunCurrentNodeId(run);
    const currentNodePosition = getStepPositionLabel(currentNodeId, nodes);
    const historyTitle = getRunHistoryTitle(runStatus);
    const historyDetail = getRunHistoryDetail(run as WorkflowRun, nodes);
    const historySubdetail = getRunHistorySubdetail(run as WorkflowRun, nodes);
    const isLive = isActiveStepStatus(runStatus) || WAITING_STATUSES.has(runStatus);

    return (
        <article
            className={cn(
                'lemma-index-row group',
                isLive && 'lemma-run-row-live'
            )}
        >
            <button
                type="button"
                onClick={onOpen}
                className="flow-execution-row-button grid w-full gap-3 px-3 py-3 md:grid-cols-[minmax(0,1fr)_8.5rem_4.5rem_4.5rem] md:items-center"
            >
                <div className="min-w-0">
                    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                        <span
                            className={cn(
                                'h-2 w-2 shrink-0 rounded-full',
                                COMPLETE_STATUSES.has(runStatus) && 'bg-[var(--state-success)]',
                                FAILURE_STATUSES.has(runStatus) && 'bg-[var(--state-error)]',
                                (WAITING_STATUSES.has(runStatus) || isLive) && 'bg-[var(--state-warning)]',
                                !COMPLETE_STATUSES.has(runStatus) && !FAILURE_STATUSES.has(runStatus) && !WAITING_STATUSES.has(runStatus) && !isLive && 'bg-[var(--text-tertiary)]'
                            )}
                        />
                        <h3 className="truncate text-sm font-semibold text-[var(--text-primary)]">{historyTitle}</h3>
                        <span className="font-mono text-xs text-[var(--text-tertiary)]">#{formatRunIdShort(runIdValue)}</span>
                        {isLive ? (
                            <span className="inline-flex items-center gap-1 text-xs text-[var(--state-warning)]">
                                <Sparkles className="h-3 w-3" />
                                Live
                            </span>
                        ) : null}
                    </div>
                    <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--text-tertiary)]">
                        <span className="text-[var(--text-secondary)]">{historyDetail}</span>
                        <span>{currentNodePosition}</span>
                        <span className="hidden min-w-0 truncate lg:inline">{historySubdetail}</span>
                        <span className="md:hidden">{runDisplayDate ? formatTimestamp(runDisplayDate) : 'Unknown time'}</span>
                        {runDuration ? <span className="md:hidden">{runDuration}</span> : null}
                    </div>
                </div>
                <span className="hidden text-right text-xs text-[var(--text-secondary)] md:block">
                    {runDisplayDate ? formatTimestamp(runDisplayDate) : 'Unknown time'}
                </span>
                <span className="hidden text-right text-xs text-[var(--text-secondary)] md:block">
                    {runDuration || '...'}
                </span>
                <span className="hidden items-center justify-end gap-1 text-xs text-[var(--text-primary)] opacity-60 transition-opacity group-hover:opacity-100 md:inline-flex">
                    Open
                    <ChevronRight className="h-4 w-4" />
                </span>
            </button>
        </article>
    );
}
