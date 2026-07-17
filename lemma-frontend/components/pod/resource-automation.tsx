'use client';

import { useState, type ReactNode } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { CalendarClock, ChevronRight, MessageCircle, Pause, Play, Plus } from '@/components/ui/icons';
import { toast } from 'sonner';

import { ProductIcon } from '@/components/pod/product-icon';
import { InlineTriggerForm, type TriggerTarget } from '@/components/pod/inline-trigger-form';
import { StartConversationButton } from '@/components/pod/start-conversation-button';
import { buildScopedConversationHref } from '@/lib/assistant/conversation-composer-context';
import { EmptyState } from '@/components/shared/empty-state';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { ResourceVisibilityBadge } from '@/components/shared/resource-visibility';
import { Button } from '@/components/ui/button';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { useDeleteSchedule, useUpdateSchedule } from '@/lib/hooks/use-schedules';
import {
    SURFACE_PLATFORM_META,
    describeReach,
    getSurfaceDeepLink,
    getSurfaceIdentity,
    getSurfacePlatformKey,
    getSurfaceStatus,
} from '@/lib/utils/surfaces';
import { describeScheduleConfig, formatScheduleType, getScheduleTargetName } from '@/lib/utils/schedules';
import { formatAgentName } from '@/lib/utils/agents';
import { ScheduleType, type Schedule } from '@/lib/types';
import type { AssistantSurface } from '@/lib/types';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Layout wrapper — a scrollable, centered pane for detail-page automation tabs.
// ---------------------------------------------------------------------------

export function AutomationPane({ children }: { children: ReactNode }) {
    return (
        <div className="h-full overflow-y-auto bg-[var(--bg-canvas)]">
            <div className="mx-auto max-w-3xl space-y-8 px-5 py-6">{children}</div>
        </div>
    );
}

function SectionHeading({
    icon,
    title,
    description,
    action,
}: {
    icon: ReactNode;
    title: string;
    description: string;
    action?: ReactNode;
}) {
    return (
        <div className="mb-4 flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-3">
                <span className="mt-0.5 shrink-0">{icon}</span>
                <div className="min-w-0">
                    <h2 className="text-base font-normal leading-snug text-[var(--text-secondary)]">{title}</h2>
                    <p className="mt-0.5 text-sm leading-6 text-[var(--text-secondary)]">{description}</p>
                </div>
            </div>
            {action ? <div className="shrink-0">{action}</div> : null}
        </div>
    );
}

// ---------------------------------------------------------------------------
// Compact one-line identity chips — for pages that want a quiet row of
// connected channels/triggers instead of a full section (e.g. agent home).
// ---------------------------------------------------------------------------

const identityChipClass = 'inline-flex max-w-[240px] items-center gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--card-bg)] py-1 pl-1 pr-2.5 shadow-[var(--shadow-xs)] transition-colors hover:border-[var(--border-strong)]';

export function SurfaceIdentityChip({
    surface,
    reachFor,
}: {
    surface: AssistantSurface;
    reachFor: string | null;
}) {
    const meta = SURFACE_PLATFORM_META[getSurfacePlatformKey(surface)]
        ?? { label: getSurfacePlatformKey(surface), logoSrc: '' };
    const identity = getSurfaceIdentity(surface);
    const deepLink = getSurfaceDeepLink(surface);
    const status = getSurfaceStatus(surface);

    const content = (
        <>
            <span className="surface-platform-mark surface-platform-mark-logo shrink-0" data-platform={getSurfacePlatformKey(surface).toLowerCase()}>
                {meta.logoSrc ? (
                    <Image src={meta.logoSrc} alt="" width={16} height={16} className="surface-platform-logo" aria-hidden="true" />
                ) : null}
                <MessageCircle className="surface-platform-icon-fallback h-4 w-4" />
            </span>
            <span className="truncate text-sm font-medium text-[var(--text-primary)]">{identity || meta.label}</span>
            <span
                className={cn(
                    'h-1.5 w-1.5 shrink-0 rounded-full',
                    status.tone === 'success' ? 'bg-[var(--state-success)]' : 'bg-[var(--text-tertiary)]',
                )}
                aria-hidden
            />
        </>
    );

    if (deepLink) {
        return (
            <a href={deepLink} target="_blank" rel="noopener noreferrer" className={identityChipClass} title={describeReach(surface, reachFor)}>
                {content}
            </a>
        );
    }
    return (
        <span className={identityChipClass} title={describeReach(surface, reachFor)}>
            {content}
        </span>
    );
}

const connectChipClass = 'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-dashed border-[var(--border-subtle)] opacity-50 transition-opacity hover:opacity-100';

/** A faded platform icon for a surface this agent doesn't reach yet — routes an
 * already-connected surface here, or sends the user to connect a new one. */
export function SurfaceConnectChip({
    platformKey,
    connectedInPod,
    manageHref,
    onRoute,
}: {
    platformKey: string;
    connectedInPod: boolean;
    manageHref: string;
    onRoute: () => void;
}) {
    const meta = SURFACE_PLATFORM_META[platformKey] ?? { label: platformKey, logoSrc: '' };
    const icon = meta.logoSrc ? (
        <Image src={meta.logoSrc} alt="" width={16} height={16} className="object-contain" aria-hidden="true" />
    ) : (
        <MessageCircle className="h-4 w-4 text-[var(--text-tertiary)]" aria-hidden />
    );

    if (connectedInPod) {
        return (
            <Button type="button" variant="ghost" size="icon" onClick={onRoute} className={connectChipClass} title={`Route ${meta.label} here`} aria-label={`Route ${meta.label} here`}>
                {icon}
            </Button>
        );
    }
    return (
        <Link href={manageHref} className={connectChipClass} title={`Connect ${meta.label}`} aria-label={`Connect ${meta.label}`}>
            {icon}
        </Link>
    );
}

export function TriggerIdentityChip({ schedule }: { schedule: Schedule }) {
    const active = schedule.is_active !== false;
    return (
        <span className={identityChipClass} title={active ? 'Active' : 'Paused'}>
            <ProductIcon
                kind={schedule.schedule_type === ScheduleType.DATASTORE ? 'data' : schedule.schedule_type === ScheduleType.WEBHOOK ? 'connectors' : 'schedules'}
                size="sm"
            />
            <span className="truncate text-sm font-medium text-[var(--text-primary)]">{describeScheduleConfig(schedule)}</span>
            <span
                className={cn('h-1.5 w-1.5 shrink-0 rounded-full', active ? 'bg-[var(--state-success)]' : 'bg-[var(--text-tertiary)]')}
                aria-hidden
            />
        </span>
    );
}

// ---------------------------------------------------------------------------
// Triggers — the schedules that wake up this agent or workflow.
// ---------------------------------------------------------------------------

export function TriggersSection({
    podId,
    target,
    schedules,
    newHref,
    description,
    canCreate,
    canUpdate,
    canDelete,
}: {
    podId: string;
    target: TriggerTarget;
    schedules: Schedule[];
    newHref: string;
    description: string;
    canCreate: boolean;
    canUpdate: boolean;
    canDelete: boolean;
}) {
    const updateSchedule = useUpdateSchedule(podId);
    const deleteSchedule = useDeleteSchedule(podId);
    const [pendingDelete, setPendingDelete] = useState<Schedule | null>(null);
    const [sheetOpen, setSheetOpen] = useState(false);
    const isMutating = updateSchedule.isPending || deleteSchedule.isPending;

    const handleToggle = async (schedule: Schedule) => {
        try {
            await updateSchedule.mutateAsync({
                scheduleId: schedule.id,
                data: { is_active: schedule.is_active === false },
            });
            toast.success(schedule.is_active === false ? 'Schedule resumed' : 'Schedule paused');
        } catch {
            toast.error('Failed to update schedule');
        }
    };

    const handleDelete = async () => {
        if (!pendingDelete) return;
        try {
            await deleteSchedule.mutateAsync(pendingDelete.id);
            toast.success('Schedule deleted');
            setPendingDelete(null);
        } catch {
            toast.error('Failed to delete schedule');
        }
    };

    return (
        <section>
            <SectionHeading
                icon={<ProductIcon kind="schedules" size="lg" />}
                title="Triggers"
                description={description}
                action={canCreate ? (
                    <Button type="button" size="sm" className="gap-2" onClick={() => setSheetOpen(true)}>
                        <Plus className="h-4 w-4" />
                        New trigger
                    </Button>
                ) : null}
            />

            {schedules.length === 0 ? (
                <EmptyState
                    variant="compact"
                    icon={<CalendarClock className="h-4 w-4" />}
                    title="No triggers yet"
                    description={canCreate
                        ? 'Add a trigger so this runs on a rhythm, an app event, or a data change — without anyone starting it.'
                        : 'Nothing wakes this up automatically yet.'}
                    action={canCreate ? (
                        <Button type="button" size="sm" variant="outline" className="gap-2" onClick={() => setSheetOpen(true)}>
                            <Plus className="h-4 w-4" />
                            New trigger
                        </Button>
                    ) : undefined}
                />
            ) : (
                <ul className="lemma-index-list">
                    {schedules.map((schedule) => (
                        <TriggerRow
                            key={schedule.id}
                            schedule={schedule}
                            isMutating={isMutating}
                            canUpdate={canUpdate}
                            canDelete={canDelete}
                            onToggle={() => void handleToggle(schedule)}
                            onDelete={() => setPendingDelete(schedule)}
                        />
                    ))}
                </ul>
            )}

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

            {canCreate ? (
                <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
                    <SheetContent side="right" className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-md">
                        <SheetHeader>
                            <SheetTitle>New trigger</SheetTitle>
                            <SheetDescription>{description}</SheetDescription>
                        </SheetHeader>
                        <InlineTriggerForm
                            podId={podId}
                            target={target}
                            moreOptionsHref={newHref}
                            onCreated={() => setSheetOpen(false)}
                            onCancel={() => setSheetOpen(false)}
                        />
                    </SheetContent>
                </Sheet>
            ) : null}
        </section>
    );
}

function TriggerRow({
    schedule,
    isMutating,
    canUpdate,
    canDelete,
    onToggle,
    onDelete,
}: {
    schedule: Schedule;
    isMutating: boolean;
    canUpdate: boolean;
    canDelete: boolean;
    onToggle: () => void;
    onDelete: () => void;
}) {
    const active = schedule.is_active !== false;
    const triggerKind = schedule.schedule_type === ScheduleType.DATASTORE
        ? 'data'
        : schedule.schedule_type === ScheduleType.WEBHOOK
            ? 'connectors'
            : 'schedules';
    const hasMenuActions = canUpdate || canDelete;

    return (
        <li className="lemma-index-row group flex items-center gap-2.5">
            <ProductIcon kind={triggerKind} size="md" />
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <p className="truncate text-sm font-medium text-[var(--text-primary)]">{describeScheduleConfig(schedule)}</p>
                    <span className="chip chip-sm chip-muted">{formatScheduleType(schedule.schedule_type)}</span>
                    <ResourceVisibilityBadge visibility={schedule.visibility} resourceLabel="schedules" hideWhenDefault />
                </div>
                {schedule.filter_instruction ? (
                    <p className="mt-0.5 truncate text-xs text-[var(--text-tertiary)]">Only when: {schedule.filter_instruction}</p>
                ) : null}
            </div>
            <span className={cn(
                'inline-flex shrink-0 items-center gap-1.5 text-xs font-medium',
                active ? 'text-[var(--state-success)]' : 'text-[var(--text-tertiary)]',
            )}>
                <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
                {active ? 'Active' : 'Paused'}
            </span>
            {hasMenuActions ? (
                <ResourceActionsMenu
                    ariaLabel="Open trigger actions"
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

// ---------------------------------------------------------------------------
// Recent conversations — shared between the agent detail page and the pod
// assistant page (same list, scoped to a named agent or the pod default).
// ---------------------------------------------------------------------------

export function formatRelativeTime(value?: string | null): string | null {
    if (!value) return null;
    const then = new Date(value).getTime();
    if (Number.isNaN(then)) return null;
    const diffSec = Math.round((Date.now() - then) / 1000);
    if (diffSec < 45) return 'just now';
    const diffMin = Math.round(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDay = Math.round(diffHr / 24);
    if (diffDay < 7) return `${diffDay}d ago`;
    const diffWk = Math.round(diffDay / 7);
    if (diffWk < 5) return `${diffWk}w ago`;
    const diffMo = Math.round(diffDay / 30);
    if (diffMo < 12) return `${diffMo}mo ago`;
    return `${Math.round(diffDay / 365)}y ago`;
}

export function RecentConversations({
    podId,
    conversations,
    agentName,
}: {
    podId: string;
    conversations: Array<{ id: string; title?: string | null; updated_at?: string; created_at?: string }>;
    /** Agent to start a new conversation with, or `null` for the pod default assistant. */
    agentName: string | null;
}) {
    if (conversations.length === 0) return null;

    return (
        <section className="mt-9">
            <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="text-base font-normal leading-snug text-[var(--text-secondary)]">Recent conversations</h2>
                <StartConversationButton podId={podId} agentName={agentName} label="New" variant="ghost" />
            </div>
            <div className="lemma-index-list">
                {conversations.map((conversation) => {
                    const timestamp = formatRelativeTime(conversation.updated_at ?? conversation.created_at);
                    return (
                        <Link
                            key={conversation.id}
                            href={buildScopedConversationHref({
                                podId,
                                conversationId: conversation.id,
                                agentName,
                            })}
                            className="lemma-index-row group flex items-center gap-2.5"
                        >
                            <MessageCircle className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" aria-hidden />
                            <span className="min-w-0 flex-1 truncate text-sm text-[var(--text-primary)]">
                                {conversation.title?.trim() || 'Untitled conversation'}
                            </span>
                            {timestamp ? (
                                <span className="hidden shrink-0 text-xs text-[var(--text-tertiary)] sm:inline">{timestamp}</span>
                            ) : null}
                            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)] opacity-0 transition-[opacity,transform] group-hover:translate-x-0.5 group-hover:opacity-100" aria-hidden />
                        </Link>
                    );
                })}
            </div>
        </section>
    );
}
