'use client';

import { useState } from 'react';
import { Plus } from '@/components/ui/icons';

import { ProductIcon } from '@/components/pod/product-icon';
import { Nothing, WiringRow } from '@/components/pod/wiring-row';
import { TriggerModal, type TriggerTarget } from '@/components/triggers/trigger-modal';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { describeScheduleConfig, formatScheduleType } from '@/lib/utils/schedules';
import { ScheduleType, type Schedule } from '@/lib/types';
import { cn } from '@/lib/utils';

/**
 * When this agent or workflow runs on its own — and the only place you change it.
 *
 * Every chip opens the trigger it describes; the button beside them adds one.
 * Neither leaves the page, because the thing being woken up is the context that
 * makes the question answerable at all.
 */
export function TriggersRow({
    podId,
    target,
    schedules,
    canCreate,
    canUpdate,
    canDelete,
    emptyText,
}: {
    podId: string;
    target: TriggerTarget;
    /** Triggers already pointed at this target, active and paused alike. */
    schedules: Schedule[];
    canCreate: boolean;
    canUpdate: boolean;
    canDelete: boolean;
    /** What "nothing wakes this up" reads as for this kind of target. */
    emptyText: string;
}) {
    // `null` = closed, `'new'` = create, an id = configure that one. An id, not
    // the object: pausing a trigger from inside the modal refetches the list,
    // and a captured object would leave the modal showing the old state.
    const [editing, setEditing] = useState<string | 'new' | null>(null);
    const editingSchedule = editing && editing !== 'new'
        ? schedules.find((schedule) => schedule.id === editing) ?? null
        : null;

    return (
        <TooltipProvider>
            <WiringRow
                label="Runs when"
                action={canCreate ? (
                    <Button type="button" variant="secondary" size="sm" onClick={() => setEditing('new')}>
                        <Plus className="h-3.5 w-3.5" />
                        Add trigger
                    </Button>
                ) : null}
            >
                {schedules.length === 0 ? (
                    <Nothing>{emptyText}</Nothing>
                ) : (
                    <div className="agent-wiring-chips">
                        {schedules.map((schedule) => (
                            <TriggerChip
                                key={schedule.id}
                                schedule={schedule}
                                onOpen={() => setEditing(schedule.id)}
                            />
                        ))}
                    </div>
                )}
            </WiringRow>

            <TriggerModal
                podId={podId}
                target={target}
                schedule={editingSchedule}
                open={editing !== null}
                onOpenChange={(open) => {
                    if (!open) setEditing(null);
                }}
                canUpdate={canUpdate}
                canDelete={canDelete}
            />
        </TooltipProvider>
    );
}

const chipClass =
    'inline-flex max-w-[240px] items-center gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--card-bg)] py-1 pl-1 pr-2.5 shadow-[var(--shadow-xs)] transition-colors hover:border-[var(--border-strong)]';

function TriggerChip({ schedule, onOpen }: { schedule: Schedule; onOpen: () => void }) {
    const active = schedule.is_active !== false;
    const iconKind = schedule.schedule_type === ScheduleType.DATASTORE
        ? 'data'
        : schedule.schedule_type === ScheduleType.WEBHOOK
            ? 'connectors'
            : 'schedules';

    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <button type="button" onClick={onOpen} className={cn('resource-chip-button', chipClass, 'custom-focus-ring')}>
                    <ProductIcon kind={iconKind} size="sm" />
                    <span className="truncate text-sm font-medium text-[var(--text-primary)]">
                        {describeScheduleConfig(schedule)}
                    </span>
                    <span
                        className={cn(
                            'h-1.5 w-1.5 shrink-0 rounded-full',
                            active ? 'bg-[var(--state-success)]' : 'bg-[var(--text-tertiary)]',
                        )}
                        aria-hidden
                    />
                </button>
            </TooltipTrigger>
            <TooltipContent>
                {formatScheduleType(schedule.schedule_type)} · {active ? 'Active' : 'Paused'}
                {String(schedule.visibility || '').toUpperCase() === 'PERSONAL' ? ' · Yours alone' : ''}
                {schedule.filter_instruction ? ` · Only when: ${schedule.filter_instruction}` : ''}
            </TooltipContent>
        </Tooltip>
    );
}
