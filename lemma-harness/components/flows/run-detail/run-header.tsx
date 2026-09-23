'use client';

// The run's chrome — declared to the pod shell, not drawn here.
//
// A run is a normal route now rather than a focus surface, so it keeps the
// sidebar, the workspace tabs and the one context bar the rest of the pod uses.
// `ResourceHeader` renders nothing: it hands the shell a title, a back target,
// meta and actions, which is what keeps that bar a single element across route
// changes instead of every page remounting its own.
//
// What the bar carries is deliberately short — name, id, status, progress, and
// the two verbs. Position lives in the log below, which was already showing it.

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { RefreshCw, RotateCcw, XCircle } from '@/components/ui/icons';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { ResourceHeader } from '@/components/pod/resource-layout';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { showResourceErrorToast } from '@/components/shared/resource-feedback';
import { StepLoader } from '@/components/brand/loader';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { useCancelFlowRun, isTerminalWorkflowRunStatus, shouldPollWorkflowRun } from '@/lib/hooks/use-flows';
import type { WorkflowRun } from '@/lib/types';
import {
    formatDuration,
    formatRunIdShort,
    formatStatusLabel,
    getRunDisplayDate,
    getStatusVariant,
    parseApiDate,
    type RunCardRun,
} from '../run-format';

export function RunHeader({
    podId,
    flowName,
    runId,
    run,
    runStatus,
    stepsDone,
    stepsTotal,
    isRefreshing,
    onRefresh,
}: {
    podId: string;
    flowName: string;
    runId: string;
    run: RunCardRun | null;
    runStatus: string;
    stepsDone: number;
    stepsTotal: number;
    isRefreshing: boolean;
    onRefresh: () => void;
}) {
    const router = useRouter();
    const [confirmCancel, setConfirmCancel] = useState(false);
    const [isRestarting, setIsRestarting] = useState(false);
    const cancelRun = useCancelFlowRun();

    const isTerminal = isTerminalWorkflowRunStatus(runStatus);
    const startedAt = run ? getRunDisplayDate(run) : null;
    const duration = formatDuration(startedAt, parseApiDate(run?.completed_at) ?? (isTerminal ? null : new Date()));

    // The client stops polling a run that has been quiet too long. That used to
    // be invisible: the page sat on "Running" forever with no hint it had
    // stopped listening. Say so, and offer the verb that fixes it.
    const isStale = Boolean(run) && !isTerminal && !shouldPollWorkflowRun(run as WorkflowRun);

    const handleCancel = async () => {
        try {
            await cancelRun.mutateAsync({ podId, flowId: flowName, runId });
            setConfirmCancel(false);
            onRefresh();
        } catch (error) {
            showResourceErrorToast(error, 'Could not cancel this run');
        }
    };

    const handleRerun = async () => {
        setIsRestarting(true);
        try {
            // A fresh run from the entry node — the engine has no notion of
            // resuming a failed run from the step that broke.
            const created = await getLemmaClient(podId).workflows.runs.create(flowName);
            const nextRunId = (created as { id?: string })?.id;
            if (nextRunId) {
                router.push(`/pod/${podId}/flows/${encodeURIComponent(flowName)}/runs/${encodeURIComponent(nextRunId)}`);
            }
        } catch (error) {
            showResourceErrorToast(error, 'Could not start a new run');
        } finally {
            setIsRestarting(false);
        }
    };

    return (
        <>
            <ResourceHeader
                // A ReactNode title means the workspace tab falls back to
                // `tabTitle`, which is what we want: the tab should read as a
                // run of this workflow, not repeat the badge row.
                title={(
                    <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate">{flowName}</span>
                        <span className="shrink-0 font-mono text-xs font-medium text-[var(--text-tertiary)]">
                            #{formatRunIdShort(runId)}
                        </span>
                        <Badge variant={getStatusVariant(runStatus)} className="flow-execution-badge-compact shrink-0">
                            {/* getStatusIcon returns a full StepLoader for RUNNING,
                                which is sized for a hero badge and swallowed the
                                bar. A chip gets a dot. */}
                            <span className={cn('run-status-dot', isTerminal ? undefined : 'run-status-dot-live')} aria-hidden />
                            {formatStatusLabel(runStatus)}
                        </Badge>
                    </span>
                )}
                tabTitle={`${flowName} run`}
                backHref={`/pod/${podId}/flows/${encodeURIComponent(flowName)}`}
                // Not the flow name: the title already says it, and the bar was
                // printing it twice in a row.
                backLabel="Workflow"
                meta={(
                    <span className="flex items-center gap-1.5 text-xs text-[var(--text-tertiary)]">
                        {stepsTotal > 0 ? <span>{stepsDone} of {stepsTotal} steps</span> : null}
                        {stepsTotal > 0 && duration ? <span aria-hidden>·</span> : null}
                        {duration ? <span>{duration}</span> : null}
                        {isStale ? (
                            <span className="text-[var(--state-warning)]">Stopped watching for updates</span>
                        ) : null}
                    </span>
                )}
                actions={(
                    <div className="flex items-center gap-2">
                        <Button
                            type="button"
                            variant="quiet"
                            size="icon"
                            className="h-8 w-8"
                            onClick={onRefresh}
                            disabled={isRefreshing}
                            aria-label="Refresh run"
                            title="Refresh run"
                        >
                            <RefreshCw className={cn('h-3.5 w-3.5', isRefreshing && 'lemma-spin')} />
                        </Button>

                        {isTerminal ? (
                            <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                className="h-8 gap-1.5 px-3 text-xs"
                                onClick={() => void handleRerun()}
                                disabled={isRestarting}
                            >
                                {isRestarting ? <StepLoader size="xs" /> : <RotateCcw className="h-3.5 w-3.5" />}
                                Run again
                            </Button>
                        ) : (
                            <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                className="h-8 gap-1.5 px-3 text-xs"
                                onClick={() => setConfirmCancel(true)}
                                disabled={!run || cancelRun.isPending}
                            >
                                <XCircle className="h-3.5 w-3.5" />
                                Cancel
                            </Button>
                        )}
                    </div>
                )}
            />

            <DestructiveConfirmationDialog
                open={confirmCancel}
                onOpenChange={setConfirmCancel}
                title="Cancel this run?"
                description="The run stops where it is and cannot be resumed. You can start a new one afterwards."
                resourceName=""
                confirmationText=""
                consequences={[
                    'Work already started outside the workflow — an agent conversation or a function run — keeps going. Its result is discarded when it finishes.',
                    'Steps that already completed keep their recorded output.',
                ]}
                confirmLabel="Cancel run"
                pendingLabel="Cancelling..."
                isPending={cancelRun.isPending}
                onConfirm={() => void handleCancel()}
            />
        </>
    );
}
