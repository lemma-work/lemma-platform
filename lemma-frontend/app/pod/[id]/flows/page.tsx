'use client';

import { use, useMemo, useState } from 'react';
import Link from 'next/link';
import {
    AlertTriangle,
    CheckCircle2,
    ChevronRight,
    Circle,
    Clock3,
    Edit2,
    ListChecks,
    MoreHorizontal,
    Play,
    Plus,
    Trash2,
    Zap,
} from 'lucide-react';
import { toast } from 'sonner';

import { ConceptHint } from '@/components/education/concept-hint';
import { SectionPrimer } from '@/components/education/section-primer';
import { EmptyState, QuietEmptyState } from '@/components/shared/empty-state';
import { ProductIcon } from '@/components/pod/product-icon';
import { Button } from '@/components/ui/button';
import { ResourceIndexHeader, ResourceIndexShell, ResourceMetricButton } from '@/components/pod/resource-layout';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { getFunctionNodeName } from '@/lib/utils/flow-node-config';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ResourceVisibilityBadge } from '@/components/shared/resource-visibility';
import {
    useDeleteFlow,
    useFlows,
    useWorkflowRunSnapshots,
    useWorkflowRunWaitAssignments,
    type WorkflowRunWaitAssignment,
} from '@/lib/hooks/use-flows';
import { useFunctions, useDeleteFunction } from '@/lib/hooks/use-functions';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { useSchedules } from '@/lib/hooks/use-schedules';
import type { Workflow as WorkflowType, Function as FunctionType, WorkflowRun } from '@/lib/types';
import { NodeType } from '@/lib/types';
import { cn } from '@/lib/utils';

type WorkflowRunItem = {
    flow: WorkflowType;
    workflowName: string;
    run: WorkflowRun;
};

type ParticipantTone = 'human' | 'ai' | 'function' | 'system';

type Participant = {
    tone: ParticipantTone;
    label: string;
};

type WorkflowView = 'workflows' | 'waiting' | 'running' | 'recent' | 'functions';
type WorkflowSort = 'az' | 'recent';

const RUNNING_STATUSES = new Set(['PENDING', 'RUNNING', 'EXECUTING', 'WAITING']);
const SUCCESS_STATUSES = new Set(['COMPLETED', 'SUCCESS', 'SUCCEEDED']);
const FAILURE_STATUSES = new Set(['FAILED', 'ERROR', 'CANCELLED', 'CANCELED']);

export default function FlowsIndexPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const podAccess = usePodAccess(podId);
    const canCreateWorkflow = podAccess.can('workflow.create');
    const canUpdateWorkflow = podAccess.can('workflow.update');
    const canExecuteWorkflow = podAccess.can('workflow.execute');
    const canDeleteWorkflow = podAccess.can('workflow.delete');
    const canCreateFunction = podAccess.can('function.create');
    const canUpdateFunction = podAccess.can('function.update');
    const canExecuteFunction = podAccess.can('function.execute');
    const canDeleteFunction = podAccess.can('function.delete');
    const { data: flowsData, isLoading: loadingFlows } = useFlows(podId);
    const { data: functionsData, isLoading: loadingFunctions } = useFunctions(podId);
    const { data: schedulesData } = useSchedules(podId, { limit: 100 });
    const { data: waitAssignmentsData, isLoading: loadingWaits } = useWorkflowRunWaitAssignments(podId, 20);
    const { mutate: deleteFlow, isPending: isDeletingFlow } = useDeleteFlow();
    const { mutate: deleteFunction, isPending: isDeletingFunction } = useDeleteFunction();
    const [workflowPendingDelete, setWorkflowPendingDelete] = useState<WorkflowType | null>(null);
    const [functionPendingDelete, setFunctionPendingDelete] = useState<FunctionType | null>(null);

    const [activeView, setActiveView] = useState<WorkflowView>('workflows');
    const [workflowSort, setWorkflowSort] = useState<WorkflowSort>('az');

    const flows = useMemo(() => flowsData || [], [flowsData]);
    const functions = useMemo(() => functionsData?.items || [], [functionsData?.items]);
    const workflowNames = useMemo(() => flows.map((flow) => flow.name), [flows]);
    const { data: runSnapshotsData } = useWorkflowRunSnapshots(podId, workflowNames, 8, { pollWhenLive: true });
    const activeSchedules = useMemo(() => (schedulesData?.items || []).filter((schedule) => schedule.is_active !== false), [schedulesData?.items]);

    const flowByIdOrName = useMemo(() => {
        const map = new Map<string, WorkflowType>();
        flows.forEach((flow) => {
            map.set(flow.id, flow);
            map.set(flow.name, flow);
        });
        return map;
    }, [flows]);

    const functionNameById = useMemo(() => {
        const map = new Map<string, string>();
        for (const fn of functions) {
            map.set(fn.id, fn.name);
            map.set(fn.name, fn.name);
        }
        return map;
    }, [functions]);

    const getFunctionNamesForFlow = (flow: WorkflowType): string[] => {
        if (!flow.nodes) return [];
        const names: string[] = [];
        for (const node of flow.nodes) {
            if (node.type === NodeType.FUNCTION) {
                const functionRef = getFunctionNodeName(node.config);
                if (functionRef) {
                    const name = functionNameById.get(functionRef);
                    if (name) names.push(name);
                }
            }
        }
        return names;
    };

    const usageCountByFunctionName = useMemo(() => {
        const counts = new Map<string, number>();
        for (const flow of flows) {
            const names = getFunctionNamesForFlow(flow);
            for (const name of new Set(names)) {
                counts.set(name, (counts.get(name) || 0) + 1);
            }
        }
        return counts;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [flows, functionNameById]);

    const activeScheduleCountByWorkflowName = useMemo(() => {
        const counts = new Map<string, number>();
        for (const schedule of activeSchedules) {
            if (!schedule.workflow_name) continue;
            counts.set(schedule.workflow_name, (counts.get(schedule.workflow_name) || 0) + 1);
        }
        return counts;
    }, [activeSchedules]);

    const runsByWorkflowName = useMemo(() => {
        const map = new Map<string, WorkflowRun[]>();
        (runSnapshotsData || []).forEach((snapshot) => {
            map.set(snapshot.workflowName, snapshot.runs);
        });
        return map;
    }, [runSnapshotsData]);

    const runItems = useMemo(() => {
        const items: WorkflowRunItem[] = [];
        (runSnapshotsData || []).forEach((snapshot) => {
            const flow = flowByIdOrName.get(snapshot.workflowName);
            if (!flow) return;
            snapshot.runs.forEach((run) => items.push({ flow, workflowName: snapshot.workflowName, run }));
        });
        return items.sort((a, b) => getRunSortTime(b.run) - getRunSortTime(a.run));
    }, [flowByIdOrName, runSnapshotsData]);

    const waitingAssignments = useMemo(() => {
        return (waitAssignmentsData?.items || [])
            .map((assignment) => ({
                assignment,
                flow: flowByIdOrName.get(assignment.wait.flow_id) || flowByIdOrName.get(assignment.run.flow_id),
            }))
            .filter((item): item is { assignment: WorkflowRunWaitAssignment; flow: WorkflowType } => Boolean(item.flow));
    }, [flowByIdOrName, waitAssignmentsData?.items]);

    const runningRuns = useMemo(() => {
        const waitingRunIds = new Set((waitAssignmentsData?.items || []).map((assignment) => assignment.run.id));
        return runItems
            .filter((item) => {
                const status = normalizeRunStatus(item.run.status);
                return RUNNING_STATUSES.has(status) && !waitingRunIds.has(item.run.id);
            });
    }, [runItems, waitAssignmentsData?.items]);

    const recentCompletedRuns = useMemo(() => {
        return runItems
            .filter((item) => {
                const status = normalizeRunStatus(item.run.status);
                return !RUNNING_STATUSES.has(status) || SUCCESS_STATUSES.has(status) || FAILURE_STATUSES.has(status);
            });
    }, [runItems]);

    const failedAttentionRuns = useMemo(() => {
        const seen = new Set<string>();
        return recentCompletedRuns
            .filter((item) => FAILURE_STATUSES.has(normalizeRunStatus(item.run.status)))
            .filter((item) => {
                const key = item.flow.name || item.workflowName;
                if (seen.has(key)) return false;
                seen.add(key);
                return true;
            })
            .slice(0, 3);
    }, [recentCompletedRuns]);

    const filteredFlows = useMemo(() => {
        return flows
            .sort((a, b) => {
                if (workflowSort === 'az') return a.name.localeCompare(b.name);
                const aTime = getRunSortTime(runsByWorkflowName.get(a.name)?.[0] || { id: a.id, created_at: a.updated_at || a.created_at || '', updated_at: a.updated_at || a.created_at || '' } as WorkflowRun);
                const bTime = getRunSortTime(runsByWorkflowName.get(b.name)?.[0] || { id: b.id, created_at: b.updated_at || b.created_at || '', updated_at: b.updated_at || b.created_at || '' } as WorkflowRun);
                return bTime - aTime;
            });
    }, [flows, workflowSort, runsByWorkflowName]);

    const filteredFunctions = useMemo(() => {
        return functions;
    }, [functions]);

    const workflowPendingDeleteScheduleCount = workflowPendingDelete
        ? activeScheduleCountByWorkflowName.get(workflowPendingDelete.name) || 0
        : 0;

    const handleDeleteFlow = () => {
        if (!workflowPendingDelete) return;
        if (!resourceAllows(workflowPendingDelete, 'workflow.delete', canDeleteWorkflow)) return;
        deleteFlow(
            { podId, id: workflowPendingDelete.name },
            {
                onSuccess: () => {
                    toast.success('Workflow deleted');
                    setWorkflowPendingDelete(null);
                },
                onError: () => toast.error('Failed to delete workflow'),
            }
        );
    };

    const handleDeleteFunction = () => {
        if (!functionPendingDelete) return;
        if (!resourceAllows(functionPendingDelete, 'function.delete', canDeleteFunction)) return;
        deleteFunction(
            { podId, name: functionPendingDelete.name },
            {
                onSuccess: () => {
                    toast.success('Function deleted');
                    setFunctionPendingDelete(null);
                },
                onError: () => toast.error('Failed to delete'),
            }
        );
    };

    if (loadingFlows || loadingFunctions) {
        return (
            <div className="context-shell min-h-full bg-transparent">
                <div className="mb-8 space-y-3">
                    <div className="h-5 w-20 animate-pulse rounded bg-[var(--bg-muted)]" />
                    <div className="h-10 w-72 animate-pulse rounded bg-[var(--bg-muted)]" />
                </div>
                <div className="space-y-5">
                    <div className="h-28 animate-pulse rounded-xl bg-[var(--bg-subtle)]" />
                    <div className="h-24 animate-pulse rounded-xl bg-[var(--bg-subtle)]" />
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                        {[1, 2, 3].map((i) => (
                            <div key={i} className="h-28 animate-pulse rounded-xl bg-[var(--bg-subtle)]" />
                        ))}
                    </div>
                </div>
            </div>
        );
    }

    const bothEmpty = flows.length === 0 && functions.length === 0;
    const attentionCount = loadingWaits ? 0 : waitingAssignments.length;
    const needsAttentionCount = attentionCount + failedAttentionRuns.length;

    return (
        <ResourceIndexShell>
            <ResourceIndexHeader
                title="Workflows"
                productIconTone="workflows"
                meta={<ConceptHint concept="flow" />}
                actions={(
                    <div className="flex items-center gap-2">
                        {canCreateFunction ? (
                            <Link href={`/pod/${podId}/functions/new`}>
                                <Button variant="outline" className="gap-2" size="sm">
                                    <Plus className="h-4 w-4" />
                                    New function
                                </Button>
                            </Link>
                        ) : null}
                        {canCreateWorkflow ? (
                            <Link href={`/pod/${podId}/flows/new`}>
                                <Button className="gap-2" size="sm">
                                    <Plus className="h-4 w-4" />
                                    New workflow
                                </Button>
                            </Link>
                        ) : null}
                    </div>
                )}
            />

            <SectionPrimer concept="flow" className="mb-4" />

            {bothEmpty && (
                <EmptyState
                    variant="panel"
                    icon={<ListChecks className="h-5 w-5" />}
                    title="No workflows yet"
                    description="Create a repeatable process with people, agents, and functions working together."
                    action={(canCreateFunction || canCreateWorkflow) ? (
                        <div className="flex items-center justify-center gap-3">
                            {canCreateFunction ? (
                                <Link href={`/pod/${podId}/functions/new`}>
                                    <Button variant="outline" className="gap-2" size="sm"><Plus className="h-4 w-4" />Create Function</Button>
                                </Link>
                            ) : null}
                            {canCreateWorkflow ? (
                                <Link href={`/pod/${podId}/flows/new`}>
                                    <Button className="gap-2" size="sm"><Plus className="h-4 w-4" />Create Workflow</Button>
                                </Link>
                            ) : null}
                        </div>
                    ) : null}
                />
            )}

            {!bothEmpty && (
                <div>
                    <div className="lemma-index-tabs flex-wrap">
                        <div className="flex flex-wrap items-center gap-1">
                            <ResourceMetricButton
                                active={activeView === 'workflows'}
                                label="Workflows"
                                count={flows.length}
                                onClick={() => setActiveView('workflows')}
                            />
                            <ResourceMetricButton
                                active={activeView === 'waiting'}
                                label="Waiting for you"
                                count={needsAttentionCount}
                                onClick={() => setActiveView('waiting')}
                            />
                            <ResourceMetricButton
                                active={activeView === 'running'}
                                label="Running"
                                count={runningRuns.length}
                                onClick={() => setActiveView('running')}
                            />
                            <ResourceMetricButton
                                active={activeView === 'recent'}
                                label="Recent runs"
                                count={recentCompletedRuns.length}
                                onClick={() => setActiveView('recent')}
                            />
                            <ResourceMetricButton
                                active={activeView === 'functions'}
                                label="Functions"
                                count={functions.length}
                                onClick={() => setActiveView('functions')}
                            />
                        </div>
                        {activeView === 'workflows' ? (
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 gap-1.5 px-2 text-xs"
                                onClick={() => setWorkflowSort(workflowSort === 'az' ? 'recent' : 'az')}
                            >
                                {workflowSort === 'az' ? 'A-Z' : 'Recent'}
                                <ChevronRight className="h-3 w-3 rotate-90" />
                            </Button>
                        ) : null}
                    </div>

                    <section className={cn(activeView === 'workflows' ? 'resource-index-grid resource-index-grid-md-2 resource-index-grid-xl-3 md:grid-cols-2 xl:grid-cols-3' : 'lemma-index-list')}>
                        {activeView === 'workflows' ? (
                            filteredFlows.length === 0 ? (
                                <EmptyState
                                    variant="compact"
                                    icon={<ListChecks className="h-4 w-4" />}
                                    title="No workflows yet"
                                    description="Create one when work needs more than one step."
                                />
                            ) : (
                                filteredFlows.map((flow) => (
                                    <WorkflowCard
                                        key={flow.name}
                                        flow={flow}
                                        podId={podId}
                                        runs={runsByWorkflowName.get(flow.name) || []}
                                        scheduleCount={activeScheduleCountByWorkflowName.get(flow.name) || 0}
                                        functionNames={getFunctionNamesForFlow(flow)}
                                        canUpdate={resourceAllows(flow, 'workflow.update', canUpdateWorkflow)}
                                        canExecute={resourceAllows(flow, 'workflow.execute', canExecuteWorkflow)}
                                        canDelete={resourceAllows(flow, 'workflow.delete', canDeleteWorkflow)}
                                        onDelete={setWorkflowPendingDelete}
                                    />
                                ))
                            )
                        ) : null}

                        {activeView === 'waiting' ? (
                            needsAttentionCount === 0 ? (
                                <QuietEmptyState icon={<CheckCircle2 className="h-4 w-4" />}>Nothing is waiting on you.</QuietEmptyState>
                            ) : (
                                <>
                                    {waitingAssignments.map(({ assignment, flow }) => (
                                        <WaitingRow
                                            key={assignment.wait.id}
                                            assignment={assignment}
                                            flow={flow}
                                            podId={podId}
                                        />
                                    ))}
                                    {failedAttentionRuns.map((item) => (
                                        <RunIndexRow
                                            key={`failed-${item.run.id}`}
                                            item={item}
                                            podId={podId}
                                            tone="failed"
                                            label="Failed"
                                        />
                                    ))}
                                </>
                            )
                        ) : null}

                        {activeView === 'running' ? (
                            runningRuns.length === 0 ? (
                                <QuietEmptyState icon={<Circle className="h-4 w-4" />}>No workflows are running right now.</QuietEmptyState>
                            ) : (
                                runningRuns.map((item) => (
                                    <RunIndexRow
                                        key={item.run.id}
                                        item={item}
                                        podId={podId}
                                        tone="running"
                                        label="Running"
                                    />
                                ))
                            )
                        ) : null}

                        {activeView === 'recent' ? (
                            recentCompletedRuns.length === 0 ? (
                                <QuietEmptyState icon={<Clock3 className="h-4 w-4" />}>No recent runs yet.</QuietEmptyState>
                            ) : (
                                recentCompletedRuns.map((item) => (
                                    <RunIndexRow
                                        key={item.run.id}
                                        item={item}
                                        podId={podId}
                                        tone={FAILURE_STATUSES.has(normalizeRunStatus(item.run.status)) ? 'failed' : 'done'}
                                        label={getRunOutcome(item)}
                                    />
                                ))
                            )
                        ) : null}

                        {activeView === 'functions' ? (
                            filteredFunctions.length === 0 ? (
                                <QuietEmptyState icon={<Zap className="h-4 w-4" />}>No functions yet.</QuietEmptyState>
                            ) : (
                                filteredFunctions.map((fn) => (
                                    <FunctionRow
                                        key={fn.id}
                                        func={fn}
                                        podId={podId}
                                        usageCount={usageCountByFunctionName.get(fn.name) || 0}
                                        canUpdate={resourceAllows(fn, 'function.update', canUpdateFunction)}
                                        canExecute={resourceAllows(fn, 'function.execute', canExecuteFunction)}
                                        canDelete={resourceAllows(fn, 'function.delete', canDeleteFunction)}
                                        onDelete={setFunctionPendingDelete}
                                    />
                                ))
                            )
                        ) : null}
                    </section>
                </div>
            )}
            <DestructiveConfirmationDialog
                open={Boolean(workflowPendingDelete)}
                onOpenChange={(open) => {
                    if (!open) setWorkflowPendingDelete(null);
                }}
                title="Delete workflow"
                description={`Delete "${workflowPendingDelete?.name ?? ''}"? This removes the workflow definition from this pod.`}
                resourceName={workflowPendingDelete?.name ?? ''}
                consequences={[
                    workflowPendingDeleteScheduleCount > 0
                        ? `${workflowPendingDeleteScheduleCount} active schedule${workflowPendingDeleteScheduleCount === 1 ? '' : 's'} target this workflow.`
                        : 'No active schedules currently target this workflow.',
                    'Runs already in history may no longer point to an editable workflow definition.',
                    'This action cannot be undone.',
                ]}
                confirmLabel="Delete workflow"
                pendingLabel="Deleting workflow..."
                isPending={isDeletingFlow}
                onConfirm={handleDeleteFlow}
            />
            <DestructiveConfirmationDialog
                open={Boolean(functionPendingDelete)}
                onOpenChange={(open) => {
                    if (!open) setFunctionPendingDelete(null);
                }}
                title="Delete function"
                description={`Delete "${functionPendingDelete?.name ?? ''}"? This removes the callable code from this pod.`}
                resourceName={functionPendingDelete?.name ?? ''}
                consequences={[
                    'Workflows and agents using this function may fail until they are updated.',
                    'Function run history may no longer point to an editable function definition.',
                    'This action cannot be undone.',
                ]}
                confirmLabel="Delete function"
                pendingLabel="Deleting function..."
                isPending={isDeletingFunction}
                onConfirm={handleDeleteFunction}
            />
        </ResourceIndexShell>
    );
}

function WaitingRow({
    assignment,
    flow,
    podId,
}: {
    assignment: WorkflowRunWaitAssignment;
    flow: WorkflowType;
    podId: string;
}) {
    const node = flow.nodes?.find((candidate) => candidate.id === assignment.wait.node_id);
    const title = node?.label || flow.name;
    return (
        <Link
            href={`/pod/${podId}/flows/${encodeURIComponent(flow.name)}`}
            className="lemma-index-row group flex items-center gap-2"
        >
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-[var(--state-error)]" />
            <div className="flex min-w-0 flex-1 items-baseline gap-2">
                <p className="truncate text-sm font-normal text-[var(--text-primary)]">{flow.name}</p>
                <p className="hidden truncate text-xs text-[var(--text-secondary)] md:block">{title}</p>
                <p className="hidden truncate text-xs text-[var(--text-tertiary)] opacity-0 transition-opacity group-hover:opacity-100 lg:block">
                    {describeWait(assignment, flow)}
                </p>
            </div>
            <span className="hidden shrink-0 text-xs text-[var(--state-error)] sm:inline">Waiting</span>
            <span className="hidden shrink-0 text-xs text-[var(--text-tertiary)] sm:inline">
                {formatRelativeTime(assignment.wait.created_at || assignment.run.updated_at || assignment.run.created_at)}
            </span>
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)] opacity-0 transition-[opacity,transform] group-hover:translate-x-0.5 group-hover:opacity-100" />
        </Link>
    );
}

function RunIndexRow({
    item,
    podId,
    tone,
    label,
}: {
    item: WorkflowRunItem;
    podId: string;
    tone: 'running' | 'done' | 'failed';
    label: string;
}) {
    const toneClass =
        tone === 'failed'
            ? 'bg-[var(--state-error)]'
            : tone === 'running'
                ? 'animate-pulse bg-[var(--state-success)]'
                : 'bg-[var(--state-success)]';
    const labelClass =
        tone === 'failed'
            ? 'text-[var(--state-error)]'
            : tone === 'running'
                ? 'text-[var(--state-success)]'
                : 'text-[var(--text-secondary)]';
    const time = item.run.completed_at || item.run.started_at || item.run.updated_at || item.run.created_at;

    return (
        <Link
            href={`/pod/${podId}/flows/${encodeURIComponent(item.workflowName)}`}
            className="lemma-index-row group flex items-center gap-2"
        >
            <span className={`h-2 w-2 shrink-0 rounded-full ${toneClass}`} />
            <div className="flex min-w-0 flex-1 items-baseline gap-2">
                <p className="truncate text-sm font-normal text-[var(--text-primary)]">{item.flow.name}</p>
                <p className="hidden truncate text-xs text-[var(--text-secondary)] md:block">{getCurrentStepLabel(item.flow, item.run)}</p>
            </div>
            <span className={`hidden shrink-0 text-xs sm:inline ${labelClass}`}>{label}</span>
            <span className="hidden shrink-0 text-xs text-[var(--text-tertiary)] sm:inline">{formatRelativeTime(time)}</span>
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)] opacity-0 transition-[opacity,transform] group-hover:translate-x-0.5 group-hover:opacity-100" />
        </Link>
    );
}

function WorkflowCard({
    flow,
    podId,
    runs,
    scheduleCount,
    functionNames,
    canUpdate,
    canExecute,
    canDelete,
    onDelete,
}: {
    flow: WorkflowType;
    podId: string;
    runs: WorkflowRun[];
    scheduleCount: number;
    functionNames: string[];
    canUpdate: boolean;
    canExecute: boolean;
    canDelete: boolean;
    onDelete: (flow: WorkflowType) => void;
}) {
    const participants = getParticipants(flow);
    const lastRun = runs[0];
    const lastRunTime = getReliableRunTimestamp(lastRun);
    const recentRunLabel = lastRunTime ? `Last run ${formatRelativeTime(lastRunTime)}` : null;
    const runCountLabel = runs.length > 0 ? `${runs.length} recent run${runs.length === 1 ? '' : 's'}` : `${functionNames.length} function${functionNames.length === 1 ? '' : 's'}`;
    const participantLabel = participants.length > 0 ? participants.map((participant) => participant.label).join(' ') : 'No participants';
    const hasMenuActions = canUpdate || canExecute || canDelete;

    return (
        <article className="resource-index-card group relative min-h-40 p-4">
            <div className="flex items-start justify-between gap-3">
                <Link
                    href={`/pod/${podId}/flows/${encodeURIComponent(flow.name)}`}
                    className="custom-focus-ring rounded-lg"
                    aria-label={`Open workflow ${flow.name}`}
                >
                    <ProductIcon tone="workflows" size="lg" />
                </Link>
                {hasMenuActions ? (
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <button
                                type="button"
                                className="flows-index-card-action-button resource-index-card-action"
                                aria-label="Workflow actions"
                            >
                                <MoreHorizontal className="h-4 w-4" />
                            </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                            {canUpdate ? (
                                <DropdownMenuItem asChild>
                                    <Link href={`/pod/${podId}/flows/${encodeURIComponent(flow.name)}?mode=edit`}>
                                        <Edit2 className="mr-2 h-4 w-4" />Edit
                                    </Link>
                                </DropdownMenuItem>
                            ) : null}
                            {canExecute ? (
                                <DropdownMenuItem asChild>
                                    <Link href={`/pod/${podId}/flows/${encodeURIComponent(flow.name)}`}>
                                        <Play className="mr-2 h-4 w-4" />Run
                                    </Link>
                                </DropdownMenuItem>
                            ) : null}
                            {canDelete ? (
                                <>
                                    {(canUpdate || canExecute) ? <DropdownMenuSeparator /> : null}
                                    <DropdownMenuItem className="text-[var(--state-error)]" onSelect={(e) => { e.preventDefault(); e.stopPropagation(); onDelete(flow); }}>
                                        <Trash2 className="mr-2 h-4 w-4" />Delete
                                    </DropdownMenuItem>
                                </>
                            ) : null}
                        </DropdownMenuContent>
                    </DropdownMenu>
                ) : null}
            </div>

            <Link
                href={`/pod/${podId}/flows/${encodeURIComponent(flow.name)}`}
                className="custom-focus-ring mt-3 block rounded-md"
            >
                <div className="min-w-0">
                    <p className="truncate font-display text-base font-semibold text-[var(--text-primary)]">{flow.name}</p>
                    <p className="mt-1 line-clamp-2 min-h-10 text-xs leading-5 text-[var(--text-secondary)]">
                        {flow.description || 'Repeatable work for humans and AI.'}
                    </p>
                </div>

                <div className="resource-step-strip mt-3">
                    {Array.from({ length: Math.min(Math.max(flow.node_count ?? flow.nodes?.length ?? 1, 1), 6) }).map((_, index) => (
                        <span
                            key={`${flow.name}-step-${index}`}
                            className="resource-step-segment"
                        />
                    ))}
                </div>

                <div className="mt-3 flex flex-wrap gap-1.5">
                    <ResourceVisibilityBadge visibility={flow.visibility} resourceLabel="workflows" hideWhenDefault />
                    <WorkflowCardPill label={`${flow.node_count ?? flow.nodes?.length ?? 0} steps`} />
                    <WorkflowCardPill label={scheduleCount ? `${scheduleCount} schedule${scheduleCount === 1 ? '' : 's'}` : recentRunLabel || 'Draft'} muted={!scheduleCount && !recentRunLabel} />
                    <WorkflowCardPill label={runCountLabel} />
                </div>

                <div className="mt-3 flex items-center justify-between text-xs text-[var(--text-tertiary)]">
                    <span className="truncate">{participantLabel}</span>
                    <span className="inline-flex items-center gap-1 font-medium text-[var(--text-secondary)] opacity-0 transition-gentle group-hover:translate-x-0.5 group-hover:opacity-100">
                        Open
                        <ChevronRight className="h-3.5 w-3.5" />
                    </span>
                </div>
            </Link>
        </article>
    );
}

function WorkflowCardPill({ label, muted }: { label: string; muted?: boolean }) {
    return (
        <span className={cn(
            'chip chip-sm',
            muted ? 'chip-muted text-[var(--text-tertiary)]' : 'state-badge-brand'
        )}>
            {label}
        </span>
    );
}

function FunctionRow({
    func,
    podId,
    usageCount,
    canUpdate,
    canExecute,
    canDelete,
    onDelete,
}: {
    func: FunctionType;
    podId: string;
    usageCount: number;
    canUpdate: boolean;
    canExecute: boolean;
    canDelete: boolean;
    onDelete: (func: FunctionType) => void;
}) {
    const hasMenuActions = canUpdate || canExecute || canDelete;

    return (
        <div className="lemma-index-row group flex items-center gap-2.5">
            <span className="state-badge-brand flex h-6 w-6 shrink-0 items-center justify-center rounded-md">
                <Zap className="h-3.5 w-3.5" />
            </span>
            <Link
                href={`/pod/${podId}/functions/${encodeURIComponent(func.name)}`}
                className="custom-focus-ring flex min-w-0 flex-1 items-baseline gap-2 rounded"
            >
                <p className="truncate text-sm font-medium text-[var(--text-primary)]">{func.name}</p>
                <p className="hidden truncate text-xs text-[var(--text-secondary)] md:block">
                    {func.description || 'No description yet'}
                </p>
            </Link>

            {func.status && func.status !== 'READY' ? (
                <span className="hidden shrink-0 text-xs text-[var(--text-tertiary)] sm:inline">
                    {func.status.toLowerCase()}
                </span>
            ) : null}
            <ResourceVisibilityBadge visibility={func.visibility} resourceLabel="functions" compact />
            <span className="hidden shrink-0 text-xs text-[var(--text-tertiary)] opacity-0 transition-opacity group-hover:opacity-100 sm:inline">
                {usageCount > 0
                    ? `Used in ${usageCount} workflow${usageCount > 1 ? 's' : ''}`
                    : 'Not used yet'}
            </span>

            <div className="flex shrink-0 items-center gap-1 opacity-0 transition-gentle group-hover:opacity-100">
                {hasMenuActions ? (
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <button
                                type="button"
                                className="flows-index-row-action-button flex h-6 w-6 items-center justify-center rounded text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
                            >
                                <MoreHorizontal className="h-3.5 w-3.5" />
                            </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                            {canUpdate ? (
                                <DropdownMenuItem asChild>
                                    <Link href={`/pod/${podId}/functions/${encodeURIComponent(func.name)}?mode=edit`}>
                                        <Edit2 className="mr-2 h-4 w-4" />Edit
                                    </Link>
                                </DropdownMenuItem>
                            ) : null}
                            {canExecute ? (
                                <DropdownMenuItem asChild>
                                    <Link href={`/pod/${podId}/functions/${encodeURIComponent(func.name)}`}>
                                        <Play className="mr-2 h-4 w-4" />Test
                                    </Link>
                                </DropdownMenuItem>
                            ) : null}
                            {canDelete ? (
                                <>
                                    {(canUpdate || canExecute) ? <DropdownMenuSeparator /> : null}
                                    <DropdownMenuItem className="text-[var(--state-error)]" onSelect={(e) => { e.preventDefault(); e.stopPropagation(); onDelete(func); }}>
                                        <Trash2 className="mr-2 h-4 w-4" />Delete
                                    </DropdownMenuItem>
                                </>
                            ) : null}
                        </DropdownMenuContent>
                    </DropdownMenu>
                ) : null}
                <Link
                    href={`/pod/${podId}/functions/${encodeURIComponent(func.name)}`}
                    className="custom-focus-ring rounded"
                    aria-label={`Open function ${func.name}`}
                >
                    <ChevronRight className="h-3.5 w-3.5 text-[var(--text-tertiary)]" />
                </Link>
            </div>
        </div>
    );
}

function normalizeRunStatus(status: unknown): string {
    return String(status || '').trim().toUpperCase();
}

function getRunSortTime(run: WorkflowRun): number {
    const timestamp = run.completed_at || run.started_at || run.updated_at || run.created_at || '';
    const parsed = Date.parse(timestamp);
    if (Number.isFinite(parsed)) return parsed;

    const uuidV7Timestamp = Number.parseInt(run.id.slice(0, 12), 16);
    return Number.isFinite(uuidV7Timestamp) ? uuidV7Timestamp : 0;
}

function getReliableRunTimestamp(run?: WorkflowRun): string | null {
    if (!run) return null;
    const candidates = [run.completed_at, run.started_at, run.updated_at, run.created_at];
    for (const candidate of candidates) {
        if (!candidate) continue;
        const parsed = Date.parse(candidate);
        if (Number.isFinite(parsed)) return candidate;
    }
    return null;
}

function formatRelativeTime(value?: string | null) {
    if (!value) return 'just now';
    const timestamp = Date.parse(value);
    if (!Number.isFinite(timestamp)) return 'just now';
    const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
    if (seconds < 60) return 'just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} hr`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days} day${days === 1 ? '' : 's'}`;
    return new Date(timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function describeWait(assignment: WorkflowRunWaitAssignment, flow: WorkflowType) {
    const payload = assignment.wait.payload || {};
    const node = flow.nodes?.find((candidate) => candidate.id === assignment.wait.node_id);
    const candidate =
        readText(payload.reason)
        || readText(payload.summary)
        || readText(payload.instruction)
        || readText(payload.prompt)
        || readText(payload.message);
    if (candidate) return candidate;

    if (node?.type === NodeType.FORM) {
        return `${node.label || 'A form step'} is waiting for your review.`;
    }

    return `${flow.name} paused at ${node?.label || 'a human step'} and needs your input.`;
}

function readText(value: unknown): string {
    return typeof value === 'string' && value.trim() ? value.trim() : '';
}

function getCurrentStepLabel(flow: WorkflowType, run: WorkflowRun) {
    const node = flow.nodes?.find((candidate) => candidate.id === run.current_node_id);
    if (node?.label) return node.label;
    const status = normalizeRunStatus(run.status);
    if (status === 'WAITING') return 'Waiting on a step';
    if (status === 'PENDING') return 'Queued';
    return 'In progress';
}

function getRunOutcome(item: WorkflowRunItem) {
    const status = normalizeRunStatus(item.run.status);
    const step = getCurrentStepLabel(item.flow, item.run);
    if (FAILURE_STATUSES.has(status)) {
        return `Failed at ${step}`;
    }
    if (SUCCESS_STATUSES.has(status)) {
        return 'Completed';
    }
    return status ? status.toLowerCase() : 'Finished';
}

function getParticipants(flow: WorkflowType): Participant[] {
    const seen = new Set<ParticipantTone>();
    const participants: Participant[] = [];

    const add = (tone: ParticipantTone, label: string) => {
        if (seen.has(tone)) return;
        seen.add(tone);
        participants.push({ tone, label });
    };

    // List (summary) responses omit the full graph; use the derived node_types.
    // Detail responses carry full nodes — fall back to their types.
    const types: string[] = flow.nodes?.length
        ? flow.nodes.map((node) => node.type)
        : (flow.node_types ?? []);

    types.forEach((type) => {
        if (type === NodeType.FORM) add('human', 'H');
        else if (type === NodeType.AGENT) add('ai', 'AI');
        else if (type === NodeType.FUNCTION) add('function', 'Fn');
        else if (type !== NodeType.END) add('system', 'S');
    });

    return participants;
}
