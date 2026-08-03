import type { ReactNode } from 'react';
import { ChevronRight, Sparkles } from '@/components/ui/icons';
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
    getRunHistoryTitle,
    getStepPositionLabel,
    isActiveStepStatus,
    parseApiDate,
    toRunStatus,
    type RunCardRun,
    type WorkflowEdgeLike,
} from './run-format';

/**
 * The steps, in order, as a list.
 *
 * The workflow's page owns this, next to the identity — the way an agent's page
 * states what it can reach. It is deliberately the only place it appears; it
 * used to also ride along inside the empty run state, which put two copies on
 * screen the moment a workflow had no runs.
 */
export function WorkflowSteps({
    nodes,
    edges,
    limit = 6,
    action,
}: {
    nodes: WorkflowNode[];
    edges: WorkflowEdgeLike[];
    limit?: number;
    /** The verb for changing them — this card is the only place they are counted. */
    action?: ReactNode;
}) {
    const overflow = Math.max(0, nodes.length - limit);

    return (
        <>
            <div className="mb-3 flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-baseline gap-2">
                    <h4 className="text-sm font-medium text-[var(--text-primary)]">Workflow steps</h4>
                    <span className="truncate text-xs text-[var(--text-tertiary)]">
                        {nodes.length === 0 ? 'None yet' : getGraphShapeLabel(nodes, edges)}
                    </span>
                </div>
                {action}
            </div>

            {nodes.length === 0 ? (
                <p className="text-sm text-[var(--text-tertiary)]">
                    Nothing yet — it has no work to do.
                </p>
            ) : null}
            <div className="lemma-index-list">
                {nodes.slice(0, limit).map((node, index) => (
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
                {overflow > 0 ? (
                    <div className="lemma-index-row px-3 py-2.5 text-xs text-[var(--text-tertiary)]">
                        {overflow} more step{overflow === 1 ? '' : 's'}
                    </div>
                ) : null}
            </div>
        </>
    );
}


export function RunListCard({
    run,
    nodes,
    onOpen,
    compact = false,
}: {
    run: RunCardRun;
    nodes: WorkflowNode[];
    onOpen: () => void;
    /**
     * Stacked instead of tabulated, for the dock. The wide form is a four-column
     * table row whose `md:` breakpoint follows the window, not the container —
     * in a 30rem dock it collapsed into overlapping columns, with the step
     * position printed twice and the tail of every sentence clipped.
     */
    compact?: boolean;
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
    const isLive = isActiveStepStatus(runStatus) || WAITING_STATUSES.has(runStatus);
    const statusDot = cn(
        'h-2 w-2 shrink-0 rounded-full',
        COMPLETE_STATUSES.has(runStatus) && 'bg-[var(--state-success)]',
        FAILURE_STATUSES.has(runStatus) && 'bg-[var(--state-error)]',
        (WAITING_STATUSES.has(runStatus) || isLive) && 'bg-[var(--state-warning)]',
        !COMPLETE_STATUSES.has(runStatus) && !FAILURE_STATUSES.has(runStatus) && !WAITING_STATUSES.has(runStatus) && !isLive && 'bg-[var(--text-tertiary)]',
    );

    if (compact) {
        return (
            <button type="button" onClick={onOpen} className="flow-run-compact group">
                <span className="flow-run-compact-head">
                    <span className={statusDot} />
                    <span className="flow-run-compact-title">{historyTitle}</span>
                    {isLive ? (
                        <span className="flow-run-compact-live">
                            <Sparkles className="h-3 w-3" />
                            Live
                        </span>
                    ) : null}
                    <span className="flow-run-compact-duration">{runDuration || '—'}</span>
                    <ChevronRight className="h-4 w-4 shrink-0 text-[var(--text-tertiary)] transition-transform group-hover:translate-x-0.5" />
                </span>
                {/* `historyDetail` already names the step and its position, so the
                    separate position line the wide row carries is dropped here.
                    On a failure it is the reason instead — the whole point of
                    scanning this list is finding which run broke and why. */}
                <span className="flow-run-compact-detail">{historyDetail}</span>
                <span className="flow-run-compact-meta">
                    <span className="font-mono">#{formatRunIdShort(runIdValue)}</span>
                    <span>{runDisplayDate ? formatTimestamp(runDisplayDate) : 'Unknown time'}</span>
                </span>
            </button>
        );
    }

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
                        <span className={statusDot} />
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
