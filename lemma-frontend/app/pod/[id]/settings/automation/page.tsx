'use client';

import { use, useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, CalendarClock, Pause, Play } from '@/components/ui/icons';
import { toast } from 'sonner';

import { PodSettingsShell } from '@/components/pod/pod-settings-shell';
import { ProductIcon } from '@/components/pod/product-icon';
import { SettingsPanel } from '@/components/settings/settings-kit';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { EmptyState } from '@/components/shared/empty-state';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { useDeleteSchedule, useSchedules, useUpdateSchedule } from '@/lib/hooks/use-schedules';
import { formatAgentName } from '@/lib/utils/agents';
import {
    describeScheduleConfig,
    formatScheduleType,
    getScheduleTargetKind,
    getScheduleTargetName,
} from '@/lib/utils/schedules';
import { ScheduleType, type Schedule } from '@/lib/types';
import { cn } from '@/lib/utils';
import { ListSkeleton } from '@/components/shared/loading';

/**
 * Every trigger in the pod, in one place.
 *
 * Triggers are created and edited on the agent or workflow they wake up — that
 * is where the question "when should this run" is actually being asked. What
 * this page answers is the other question, the one no single agent page can:
 * *everything* this pod does on its own, so an admin can audit it, pause it, or
 * find the one that has been quietly firing all week.
 */

type ScheduleFilter = 'all' | 'active' | 'paused';

export default function PodAutomationSettingsPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const podAccess = usePodAccess(podId);
    const canUpdateSchedule = podAccess.can('schedule.update');
    const canDeleteSchedule = podAccess.can('schedule.delete');

    const { data: schedulesData, isLoading } = useSchedules(podId, { limit: 100 });
    const updateSchedule = useUpdateSchedule(podId);
    const deleteSchedule = useDeleteSchedule(podId);

    const [filter, setFilter] = useState<ScheduleFilter>('all');
    const [pendingDelete, setPendingDelete] = useState<Schedule | null>(null);

    const schedules = useMemo(() => schedulesData?.items || [], [schedulesData?.items]);
    const activeCount = schedules.filter((schedule) => schedule.is_active !== false).length;
    const filtered = useMemo(() => schedules.filter((schedule) => {
        if (filter === 'active') return schedule.is_active !== false;
        if (filter === 'paused') return schedule.is_active === false;
        return true;
    }), [filter, schedules]);

    const isMutating = updateSchedule.isPending || deleteSchedule.isPending;

    const handleToggle = async (schedule: Schedule) => {
        if (!resourceAllows(schedule, 'schedule.update', canUpdateSchedule)) return;
        try {
            await updateSchedule.mutateAsync({
                scheduleId: schedule.id,
                data: { is_active: schedule.is_active === false },
            });
            toast.success(schedule.is_active === false ? 'Trigger resumed' : 'Trigger paused');
        } catch {
            toast.error('Failed to update trigger');
        }
    };

    const handleDelete = async () => {
        if (!pendingDelete) return;
        if (!resourceAllows(pendingDelete, 'schedule.delete', canDeleteSchedule)) return;
        try {
            await deleteSchedule.mutateAsync(pendingDelete.id);
            toast.success('Trigger deleted');
            setPendingDelete(null);
        } catch {
            toast.error('Failed to delete trigger');
        }
    };

    return (
        <PodSettingsShell
            podId={podId}
            title="Automation"
            stats={[
                { label: 'Triggers', value: String(schedules.length) },
                { label: 'Active', value: String(activeCount) },
            ]}
        >
            <SettingsPanel
                title="Triggers"
                description="Everything this pod does without being asked. Each run borrows the access of whoever set it — or, for a change on a table with row-level security, of the person that record belongs to. Add or change a trigger on the agent or workflow it wakes up."
            >
                <div className="lemma-index-tabs lemma-index-tabs-left mb-4 flex-wrap">
                    {([
                        { value: 'all' as const, label: 'All', count: schedules.length },
                        { value: 'active' as const, label: 'Active', count: activeCount },
                        { value: 'paused' as const, label: 'Paused', count: schedules.length - activeCount },
                    ]).map((tab) => (
                        <button
                            key={tab.value}
                            type="button"
                            onClick={() => setFilter(tab.value)}
                            className="choice-chip choice-chip-sm"
                            data-active={filter === tab.value ? 'true' : undefined}
                        >
                            {tab.label} · {tab.count}
                        </button>
                    ))}
                </div>

                {isLoading ? (
                    <ListSkeleton rows={5} />
                ) : filtered.length === 0 ? (
                    <EmptyState
                        variant="region"
                        icon={<CalendarClock className="h-4 w-4" />}
                        title={filter === 'paused' ? 'Nothing paused' : filter === 'active' ? 'Nothing running on its own' : 'No triggers yet'}
                        description="Open an agent or workflow and use “Runs when” to give it a rhythm, an app event, or a data change to wake up on."
                    />
                ) : (
                    <ul className="lemma-index-list">
                        {filtered.map((schedule) => (
                            <TriggerLedgerRow
                                key={schedule.id}
                                podId={podId}
                                schedule={schedule}
                                isMutating={isMutating}
                                canUpdate={resourceAllows(schedule, 'schedule.update', canUpdateSchedule)}
                                canDelete={resourceAllows(schedule, 'schedule.delete', canDeleteSchedule)}
                                onToggle={() => void handleToggle(schedule)}
                                onDelete={() => setPendingDelete(schedule)}
                            />
                        ))}
                    </ul>
                )}
            </SettingsPanel>

            <DestructiveConfirmationDialog
                open={Boolean(pendingDelete)}
                onOpenChange={(open) => {
                    if (!open) setPendingDelete(null);
                }}
                title="Delete trigger"
                description={`Delete this trigger for ${pendingDelete ? formatAgentName(getScheduleTargetName(pendingDelete)) : 'this target'}?`}
                resourceName={pendingDelete ? formatAgentName(getScheduleTargetName(pendingDelete)) : 'trigger'}
                confirmationText=""
                consequences={[
                    'This stops future automatic runs.',
                    'Existing run history is not deleted.',
                ]}
                confirmLabel="Delete trigger"
                pendingLabel="Deleting trigger..."
                isPending={deleteSchedule.isPending}
                onConfirm={() => void handleDelete()}
            />
        </PodSettingsShell>
    );
}

function getTargetHref(podId: string, schedule: Schedule): string | null {
    if (schedule.workflow_name) return `/pod/${podId}/flows/${encodeURIComponent(schedule.workflow_name)}`;
    if (schedule.agent_name) return `/pod/${podId}/agents/${encodeURIComponent(schedule.agent_name)}`;
    return null;
}

function TriggerLedgerRow({
    podId,
    schedule,
    isMutating,
    canUpdate,
    canDelete,
    onToggle,
    onDelete,
}: {
    podId: string;
    schedule: Schedule;
    isMutating: boolean;
    canUpdate: boolean;
    canDelete: boolean;
    onToggle: () => void;
    onDelete: () => void;
}) {
    const active = schedule.is_active !== false;
    const targetKind = getScheduleTargetKind(schedule);
    const targetName = formatAgentName(getScheduleTargetName(schedule));
    const targetHref = getTargetHref(podId, schedule);
    const triggerIconKind = schedule.schedule_type === ScheduleType.DATASTORE
        ? 'data'
        : schedule.schedule_type === ScheduleType.WEBHOOK
            ? 'connectors'
            : 'schedules';

    const body = (
        <>
            <ProductIcon kind={triggerIconKind} size="md" />
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="truncate text-sm font-medium text-[var(--text-primary)]">
                        {describeScheduleConfig(schedule)}
                    </span>
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" aria-hidden />
                    <span className="inline-flex min-w-0 items-center gap-1 text-sm text-[var(--text-secondary)]">
                        <ProductIcon kind={targetKind === 'agent' ? 'agents' : 'workflows'} size="xs" />
                        <span className="truncate">{targetName}</span>
                    </span>
                    <span className="chip chip-sm chip-muted">{formatScheduleType(schedule.schedule_type)}</span>
                    {/* Not the generic visibility badge: its copy is about who
                        can *open* a thing, and nobody opens a trigger. What an
                        admin auditing this list needs is whether it is the
                        pod's one trigger or somebody's own. */}
                    {String(schedule.visibility || '').toUpperCase() === 'PERSONAL' ? (
                        <span className="chip chip-sm chip-muted">Personal</span>
                    ) : null}
                </div>
                {schedule.filter_instruction ? (
                    <p className="mt-0.5 truncate text-xs text-[var(--text-tertiary)]">
                        Only when: {schedule.filter_instruction}
                    </p>
                ) : null}
            </div>
        </>
    );

    return (
        <li className="lemma-index-row group flex items-center gap-2.5">
            {targetHref ? (
                <Link href={targetHref} className="flex min-w-0 flex-1 items-center gap-2.5">
                    {body}
                </Link>
            ) : (
                <div className="flex min-w-0 flex-1 items-center gap-2.5">{body}</div>
            )}

            <span className={cn(
                'inline-flex shrink-0 items-center gap-1.5 text-xs font-medium',
                active ? 'text-[var(--state-success)]' : 'text-[var(--text-tertiary)]',
            )}>
                <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
                {active ? 'Active' : 'Paused'}
            </span>

            {canUpdate || canDelete ? (
                <ResourceActionsMenu
                    ariaLabel={`Open actions for the ${targetName} trigger`}
                    align="end"
                    triggerClassName="h-7 w-7 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                >
                    {canUpdate ? (
                        <DropdownMenuItem
                            disabled={isMutating}
                            onSelect={(event) => {
                                event.preventDefault();
                                onToggle();
                            }}
                        >
                            {active ? <Pause className="mr-2 h-4 w-4" /> : <Play className="mr-2 h-4 w-4" />}
                            {active ? 'Pause trigger' : 'Resume trigger'}
                        </DropdownMenuItem>
                    ) : null}
                    {canDelete ? (
                        <DestructiveResourceActionItem disabled={isMutating} onSelect={onDelete}>
                            Delete trigger
                        </DestructiveResourceActionItem>
                    ) : null}
                </ResourceActionsMenu>
            ) : null}
        </li>
    );
}
