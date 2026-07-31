'use client';

import { useEffect, useMemo, useState } from 'react';
import { Loader2, Pause, Play, Sparkles } from '@/components/ui/icons';
import { toast } from 'sonner';

import { ProductIcon } from '@/components/pod/product-icon';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useAccounts, useConnectors, useTriggers } from '@/lib/hooks/use-connectors';
import { useTables } from '@/lib/hooks/use-datastores';
import { useFlow } from '@/lib/hooks/use-flows';
import { usePod } from '@/lib/hooks/use-pods';
import { useCreateSchedule, useDeleteSchedule, useUpdateSchedule } from '@/lib/hooks/use-schedules';
import {
    buildCronExpression,
    describeCron,
    getScheduleDatastoreConfig,
    getScheduleTimeConfig,
    getScheduleWebhookConfig,
    parseCronExpression,
    type DataOperation,
    type TimeCadence,
} from '@/lib/utils/schedules';
import { formatAgentName } from '@/lib/utils/agents';
import { ScheduleType, type Account, type CreateScheduleRequest, type Schedule, type Workflow } from '@/lib/types';
import { cn } from '@/lib/utils';

/**
 * A trigger, set up where the thing it wakes up lives.
 *
 * Scheduling used to be a destination: you left the agent you were looking at,
 * went to a page that asked you which agent you meant, and came back. The target
 * is the context here, so the modal never asks for it — it asks what should
 * start the work, and everything else follows from that answer.
 *
 *   kind  →  details        (a trigger that does not exist yet)
 *   details                 (one that does — opened straight on its settings)
 */

export type TriggerTargetKind = 'agent' | 'workflow';
export interface TriggerTarget {
    kind: TriggerTargetKind;
    name: string;
}

type TriggerKind = `${ScheduleType}`;
type Step = 'kind' | 'details';

const WEEKDAY_OPTIONS = [
    { value: '1', label: 'Mon' },
    { value: '2', label: 'Tue' },
    { value: '3', label: 'Wed' },
    { value: '4', label: 'Thu' },
    { value: '5', label: 'Fri' },
    { value: '6', label: 'Sat' },
    { value: '0', label: 'Sun' },
] as const;

const TIMEZONES = ['UTC', 'Asia/Kolkata', 'America/New_York', 'America/Los_Angeles', 'Europe/London'] as const;

const CADENCES: Array<{ value: TimeCadence; label: string }> = [
    { value: 'hourly', label: 'Hourly' },
    { value: 'daily', label: 'Daily' },
    { value: 'weekdays', label: 'Weekdays' },
    { value: 'weekly', label: 'Weekly' },
    { value: 'monthly', label: 'Monthly' },
    { value: 'custom', label: 'Custom' },
];

const DATA_OPERATION_OPTIONS: Array<{ value: DataOperation; label: string }> = [
    { value: 'INSERT', label: 'Created' },
    { value: 'UPDATE', label: 'Updated' },
    { value: 'DELETE', label: 'Deleted' },
];

const TRIGGER_KINDS: Array<{
    value: TriggerKind;
    label: string;
    description: string;
    iconKind: 'schedules' | 'data' | 'connectors';
}> = [
    {
        value: ScheduleType.TIME,
        label: 'On a rhythm',
        description: 'Hourly, daily, weekly — a clock starts it.',
        iconKind: 'schedules',
    },
    {
        value: ScheduleType.DATASTORE,
        label: 'On a data change',
        description: 'A row is created, changed, or removed.',
        iconKind: 'data',
    },
    {
        value: ScheduleType.WEBHOOK,
        label: 'On an app event',
        description: 'Something happens in a connected app.',
        iconKind: 'connectors',
    },
];

function getEventStart(workflow: Workflow | undefined): { connector_id?: string; connector_trigger_id?: string } | null {
    if (workflow?.start?.type !== 'EVENT' || !workflow.start.config) return null;
    return workflow.start.config as { connector_id?: string; connector_trigger_id?: string };
}

function getTriggerLabel(trigger: { id: string } & Record<string, unknown>): string {
    return String(trigger.name || trigger.title || trigger.event_type || trigger.description || trigger.id);
}

function getAccountLabel(account: Account): string {
    return account.display_name || account.email || account.id;
}

export function TriggerModal({
    podId,
    target,
    schedule,
    open,
    onOpenChange,
    canUpdate = true,
    canDelete = true,
}: {
    podId: string;
    /** What this trigger wakes up. Never asked for — it is the page you came from. */
    target: TriggerTarget;
    /** Present when editing an existing trigger; absent when creating one. */
    schedule?: Schedule | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    canUpdate?: boolean;
    canDelete?: boolean;
}) {
    const isEditing = Boolean(schedule);
    const targetLabel = formatAgentName(target.name);

    const { data: pod } = usePod(podId);
    const { data: tablesData, isLoading: loadingTables } = useTables(open ? podId : undefined);
    const tables = useMemo(() => tablesData?.items || [], [tablesData?.items]);
    // Only workflows need their start read back; an agent names its own event.
    const { data: workflow } = useFlow(
        open && target.kind === 'workflow' ? podId : undefined,
        target.kind === 'workflow' ? target.name : undefined,
    );
    const workflowEventStart = getEventStart(workflow);

    const [step, setStep] = useState<Step>('kind');
    const [kind, setKind] = useState<TriggerKind>(ScheduleType.TIME);
    const [cadence, setCadence] = useState<TimeCadence>('weekdays');
    const [timeOfDay, setTimeOfDay] = useState('09:00');
    const [weeklyDays, setWeeklyDays] = useState<string[]>(['1']);
    const [monthDay, setMonthDay] = useState(1);
    const [customCron, setCustomCron] = useState('0 9 * * 1-5');
    const [timezone, setTimezone] = useState('UTC');
    const [tableName, setTableName] = useState('');
    const [dataOperations, setDataOperations] = useState<DataOperation[]>(['INSERT']);
    const [connectorId, setConnectorId] = useState('');
    const [triggerId, setTriggerId] = useState('');
    const [accountId, setAccountId] = useState('');
    const [condition, setCondition] = useState('');
    const [visibility, setVisibility] = useState('POD');
    const [confirmDelete, setConfirmDelete] = useState(false);

    /**
     * Reset on open, hydrating from the trigger being edited. Keyed on the
     * schedule id rather than the object so a background list refetch — which
     * returns an equal-but-new object — cannot wipe a half-finished edit.
     */
    const scheduleId = schedule?.id ?? null;
    useEffect(() => {
        if (!open) return;

        setConfirmDelete(false);

        if (!schedule) {
            setStep('kind');
            setKind(ScheduleType.TIME);
            setCadence('weekdays');
            setTimeOfDay('09:00');
            setWeeklyDays(['1']);
            setMonthDay(1);
            setCustomCron('0 9 * * 1-5');
            setTimezone('UTC');
            setTableName('');
            setDataOperations(['INSERT']);
            setConnectorId('');
            setTriggerId('');
            setAccountId('');
            setCondition('');
            setVisibility('POD');
            return;
        }

        setStep('details');
        setKind(schedule.schedule_type as TriggerKind);
        setCondition(schedule.filter_instruction || '');
        setVisibility(schedule.visibility || 'POD');

        if (schedule.schedule_type === ScheduleType.TIME) {
            const { cron, timezone: storedTimezone } = getScheduleTimeConfig(schedule);
            const parsed = parseCronExpression(cron);
            setCadence(parsed.cadence);
            setTimeOfDay(parsed.timeOfDay);
            setWeeklyDays(parsed.weeklyDays);
            setMonthDay(parsed.monthDay);
            setCustomCron(parsed.customCron || cron);
            setTimezone(storedTimezone);
        } else if (schedule.schedule_type === ScheduleType.DATASTORE) {
            const { tableName: storedTable, operations } = getScheduleDatastoreConfig(schedule);
            setTableName(storedTable);
            setDataOperations(operations.length ? operations : ['INSERT']);
        } else {
            const { connectorId: storedConnector, triggerId: storedTrigger } = getScheduleWebhookConfig(schedule);
            setConnectorId(storedConnector);
            setTriggerId(storedTrigger);
            setAccountId(schedule.account_id || '');
        }
        // `schedule` is read only to seed the form; `scheduleId` is the identity
        // that should re-seed it.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, scheduleId]);

    const wantsConnectors = open && step === 'details' && kind === ScheduleType.WEBHOOK;
    const { data: connectors = [] } = useConnectors({ limit: 100, enabled: wantsConnectors });
    const { data: accounts = [] } = useAccounts({
        organizationId: pod?.organization_id,
        limit: 100,
        enabled: wantsConnectors,
    });
    // A workflow's event comes from its start; only an agent picks one here.
    const eventConnectorId = target.kind === 'workflow'
        ? workflowEventStart?.connector_id || ''
        : connectorId;
    const { data: connectorTriggers = [] } = useTriggers({
        organizationId: pod?.organization_id,
        connectorId: eventConnectorId,
        limit: 100,
        enabled: wantsConnectors && target.kind === 'agent' && Boolean(connectorId),
    });

    const compatibleAccounts = useMemo(() => {
        if (!eventConnectorId) return accounts;
        return accounts.filter((account) => account.connector_id === eventConnectorId);
    }, [accounts, eventConnectorId]);
    const selectedAccountId = accountId && compatibleAccounts.some((account) => account.id === accountId)
        ? accountId
        : compatibleAccounts.find((account) => account.is_default)?.id ?? compatibleAccounts[0]?.id ?? '';
    const selectedTriggerId = triggerId && connectorTriggers.some((entry) => entry.id === triggerId)
        ? triggerId
        : connectorTriggers[0]?.id ?? '';
    const selectedTable = tableName || tables[0]?.name || '';
    const cron = buildCronExpression({ cadence, timeOfDay, weeklyDays, monthDay, customCron });

    const createSchedule = useCreateSchedule(podId);
    const updateSchedule = useUpdateSchedule(podId);
    const deleteSchedule = useDeleteSchedule(podId);
    const isSaving = createSchedule.isPending || updateSchedule.isPending;
    const isMutating = isSaving || deleteSchedule.isPending;

    // A workflow can only listen to the app event its start already names —
    // rendered as a disabled option with the reason rather than a failed save.
    const workflowEventBlocked = target.kind === 'workflow'
        && !(workflowEventStart?.connector_id && workflowEventStart.connector_trigger_id);
    const kindBlockedReason = (value: TriggerKind): string | null => {
        // Not while they are still arriving — a slow list would otherwise read
        // as "this pod has no tables" for the first frame after opening.
        if (value === ScheduleType.DATASTORE && !loadingTables && tables.length === 0) {
            return 'This pod has no tables yet.';
        }
        if (value === ScheduleType.WEBHOOK && workflowEventBlocked) {
            return 'This workflow does not start from an app event — set that on its Edit canvas first.';
        }
        return null;
    };

    const detailsReady = kind === ScheduleType.TIME
        ? Boolean(cron.trim())
        : kind === ScheduleType.DATASTORE
            ? Boolean(selectedTable) && dataOperations.length > 0
            : isEditing
                ? true
                : target.kind === 'agent'
                    ? Boolean(connectorId && selectedTriggerId && selectedAccountId)
                    : Boolean(!workflowEventBlocked && selectedAccountId);

    const buildConfig = (): Record<string, unknown> | null => {
        if (kind === ScheduleType.TIME) {
            return { schedule_type: 'CRON', cron_expression: cron.trim(), timezone: timezone.trim() || 'UTC' };
        }
        if (kind === ScheduleType.DATASTORE) {
            return { table_name: selectedTable, operations: dataOperations };
        }
        if (target.kind === 'agent') {
            return { connector_id: connectorId, connector_trigger_id: selectedTriggerId, trigger_config: {} };
        }
        // Workflow webhooks derive their connector + event from the workflow start.
        return {};
    };

    const handleSave = async () => {
        if (!detailsReady) return;

        try {
            if (schedule) {
                await updateSchedule.mutateAsync({
                    scheduleId: schedule.id,
                    data: {
                        // A webhook's app/event/account are fixed at creation, so
                        // only the parts the API can actually change are sent.
                        ...(kind === ScheduleType.WEBHOOK ? {} : { config: buildConfig() as Record<string, unknown> }),
                        // Empty string, not null: the API drops nulls, so a null
                        // here would silently leave a removed condition in place.
                        filter_instruction: condition.trim(),
                        // Only when it actually changed, so a trigger saved with
                        // a visibility this modal does not offer keeps it — and
                        // never for a data trigger, whose choice is not shown
                        // and so was never the reader's to change.
                        ...(kind === ScheduleType.DATASTORE || visibility === schedule.visibility
                            ? {}
                            : { visibility }),
                    },
                });
                toast.success('Trigger updated');
            } else {
                const payload: CreateScheduleRequest = {
                    schedule_type: kind as ScheduleType,
                    workflow_name: target.kind === 'workflow' ? target.name : null,
                    agent_name: target.kind === 'agent' ? target.name : null,
                    account_id: kind === ScheduleType.WEBHOOK ? (selectedAccountId || null) : null,
                    connector_trigger_id: kind === ScheduleType.WEBHOOK && target.kind === 'agent'
                        ? selectedTriggerId
                        : null,
                    config: buildConfig() as Record<string, unknown>,
                    filter_instruction: condition.trim() || null,
                    filter_output_schema: null,
                    // A data trigger is the pod's, always — see `RunsAsField`.
                    visibility: (kind === ScheduleType.DATASTORE ? 'POD' : visibility) as never,
                };
                await createSchedule.mutateAsync(payload);
                toast.success('Trigger created');
            }
            onOpenChange(false);
        } catch (error) {
            toast.error(error instanceof Error ? error.message : 'Failed to save trigger');
        }
    };

    const handleToggleActive = async () => {
        if (!schedule) return;
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
        if (!schedule) return;
        try {
            await deleteSchedule.mutateAsync(schedule.id);
            toast.success('Trigger deleted');
            setConfirmDelete(false);
            onOpenChange(false);
        } catch {
            toast.error('Failed to delete trigger');
        }
    };

    const active = schedule ? schedule.is_active !== false : true;
    const kindMeta = TRIGGER_KINDS.find((entry) => entry.value === kind) ?? TRIGGER_KINDS[0];
    const promise = step === 'kind'
        ? `What should start ${targetLabel} without anyone asking?`
        : isEditing
            ? `${targetLabel} runs ${kindMeta.label.replace(/^On /, 'on ')}.`
            : `${targetLabel} will run ${kindMeta.label.replace(/^On /, 'on ')}.`;

    return (
        <>
            <Dialog open={open} onOpenChange={onOpenChange}>
                <DialogContent className={cn('gap-0 p-0', step === 'kind' ? 'sm:max-w-md' : 'sm:max-w-lg')}>
                    {/* `pr-12` keeps the status pill and overflow out from under
                        the dialog's own close button, which is absolute. */}
                    <DialogHeader className="space-y-1.5 border-b border-[var(--border-subtle)] py-4 pl-5 pr-12 text-left">
                        <div className="flex items-center gap-2.5">
                            <ProductIcon kind={kindMeta.iconKind} size="sm" />
                            <DialogTitle className="flex-1 text-base font-normal">
                                {isEditing ? 'Trigger' : 'New trigger'}
                            </DialogTitle>
                            {isEditing ? (
                                <span className={cn(
                                    'inline-flex shrink-0 items-center gap-1.5 text-xs font-medium',
                                    active ? 'text-[var(--state-success)]' : 'text-[var(--text-tertiary)]',
                                )}>
                                    <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
                                    {active ? 'Active' : 'Paused'}
                                </span>
                            ) : null}
                            {/* Pausing and deleting sit here, not beside Save, so
                                the footer keeps exactly one primary verb. */}
                            {isEditing && (canUpdate || canDelete) ? (
                                <ResourceActionsMenu ariaLabel="Open trigger actions" align="end" triggerClassName="h-7 w-7 shrink-0">
                                    {canUpdate ? (
                                        <DropdownMenuItem
                                            disabled={isMutating}
                                            onSelect={(event) => {
                                                event.preventDefault();
                                                void handleToggleActive();
                                            }}
                                        >
                                            {active ? <Pause className="mr-2 h-4 w-4" /> : <Play className="mr-2 h-4 w-4" />}
                                            {active ? 'Pause trigger' : 'Resume trigger'}
                                        </DropdownMenuItem>
                                    ) : null}
                                    {canDelete ? (
                                        <DestructiveResourceActionItem
                                            disabled={isMutating}
                                            onSelect={() => setConfirmDelete(true)}
                                        >
                                            Delete trigger
                                        </DestructiveResourceActionItem>
                                    ) : null}
                                </ResourceActionsMenu>
                            ) : null}
                        </div>
                        <DialogDescription className="text-sm leading-6">{promise}</DialogDescription>
                    </DialogHeader>

                    <div className="max-h-[min(28rem,60vh)] overflow-y-auto px-5 py-4">
                        {step === 'kind' ? (
                            <div className="space-y-2">
                                {TRIGGER_KINDS.map((option) => {
                                    const blocked = kindBlockedReason(option.value);
                                    return (
                                        <button
                                            key={option.value}
                                            type="button"
                                            disabled={Boolean(blocked)}
                                            onClick={() => {
                                                setKind(option.value);
                                                setStep('details');
                                            }}
                                            className={cn(
                                                'resource-option-button custom-focus-ring flex w-full items-start gap-3 rounded-lg border p-3 text-left transition-gentle',
                                                blocked
                                                    ? 'cursor-not-allowed border-transparent opacity-55'
                                                    : 'resource-option-hover border-transparent',
                                            )}
                                        >
                                            <ProductIcon kind={option.iconKind} size="md" />
                                            <span className="min-w-0 flex-1">
                                                <span className="block text-sm font-medium text-[var(--text-primary)]">{option.label}</span>
                                                <span className="mt-0.5 block text-xs leading-5 text-[var(--text-secondary)]">
                                                    {blocked || option.description}
                                                </span>
                                            </span>
                                        </button>
                                    );
                                })}
                            </div>
                        ) : (
                            <div className="space-y-5">
                                {kind === ScheduleType.TIME ? (
                                    <TimeFields
                                        cadence={cadence}
                                        onCadenceChange={setCadence}
                                        timeOfDay={timeOfDay}
                                        onTimeOfDayChange={setTimeOfDay}
                                        weeklyDays={weeklyDays}
                                        onWeeklyDaysChange={setWeeklyDays}
                                        monthDay={monthDay}
                                        onMonthDayChange={setMonthDay}
                                        customCron={customCron}
                                        onCustomCronChange={setCustomCron}
                                        timezone={timezone}
                                        onTimezoneChange={setTimezone}
                                        cron={cron}
                                    />
                                ) : null}

                                {kind === ScheduleType.DATASTORE ? (
                                    <DataFields
                                        tables={tables.map((table) => table.name)}
                                        tableName={selectedTable}
                                        onTableChange={setTableName}
                                        operations={dataOperations}
                                        onOperationsChange={setDataOperations}
                                    />
                                ) : null}

                                {kind === ScheduleType.WEBHOOK ? (
                                    <EventFields
                                        targetKind={target.kind}
                                        isEditing={isEditing}
                                        connectors={connectors.map((connector) => ({
                                            id: connector.id,
                                            label: connector.title || connector.name || connector.id,
                                        }))}
                                        connectorId={connectorId}
                                        onConnectorChange={(value) => {
                                            setConnectorId(value);
                                            setTriggerId('');
                                        }}
                                        triggers={connectorTriggers.map((entry) => ({
                                            id: entry.id,
                                            label: getTriggerLabel(entry as { id: string } & Record<string, unknown>),
                                        }))}
                                        triggerId={selectedTriggerId}
                                        onTriggerChange={setTriggerId}
                                        accounts={compatibleAccounts}
                                        accountId={selectedAccountId}
                                        onAccountChange={setAccountId}
                                        workflowEvent={workflowEventStart}
                                        workflowEventBlocked={workflowEventBlocked}
                                    />
                                ) : null}

                                <div className="space-y-1.5">
                                    {/* "Optional" in the label carries what a
                                        helper line used to say — blank means
                                        every time — so the field is two
                                        elements, not three. */}
                                    <Label className="text-xs">
                                        Only run when
                                        <span className="ml-1.5 font-normal text-[var(--text-tertiary)]">optional</span>
                                    </Label>
                                    <Textarea
                                        value={condition}
                                        onChange={(event) => setCondition(event.target.value)}
                                        placeholder="e.g. only if the record is high priority and has an owner"
                                        className="min-h-16 resize-y"
                                    />
                                </div>

                                <RunsAsField
                                    kind={kind}
                                    targetLabel={targetLabel}
                                    visibility={visibility}
                                    onVisibilityChange={setVisibility}
                                />
                            </div>
                        )}
                    </div>

                    <DialogFooter className="items-center gap-2 border-t border-[var(--border-subtle)] px-5 py-3 sm:justify-between">
                        {/* Back exists only mid-journey — an existing trigger has
                            nowhere behind it to go. */}
                        {step === 'details' && !isEditing ? (
                            <Button type="button" variant="ghost" size="sm" onClick={() => setStep('kind')} disabled={isSaving}>
                                Back
                            </Button>
                        ) : <span />}
                        <div className="flex items-center gap-2">
                            <Button type="button" variant="ghost" size="sm" onClick={() => onOpenChange(false)} disabled={isSaving}>
                                Cancel
                            </Button>
                            {step === 'details' ? (
                                <Button
                                    type="button"
                                    size="sm"
                                    className="gap-1.5"
                                    onClick={() => void handleSave()}
                                    disabled={!detailsReady || isSaving || (isEditing && !canUpdate)}
                                >
                                    {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                                    {isEditing ? 'Save' : 'Create trigger'}
                                </Button>
                            ) : null}
                        </div>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <DestructiveConfirmationDialog
                open={confirmDelete}
                onOpenChange={setConfirmDelete}
                title="Delete trigger"
                description={`Delete this trigger for ${targetLabel}?`}
                resourceName="trigger"
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
        </>
    );
}

/**
 * Whose access a run gets — and, as the same answer, whether this is the pod's
 * one trigger or everybody's own.
 *
 * This replaced a generic share control, which asked the wrong question twice
 * over: a trigger has no page to open, so "anyone with the link" meant nothing,
 * and the choice that *does* matter was hidden inside it unexplained. Nobody
 * runs a trigger, so the identity it borrows is the only thing that decides
 * what it can read and write — which makes it the fact worth stating before
 * anyone commits, not a setting to discover afterwards.
 */
function RunsAsField({
    kind,
    targetLabel,
    visibility,
    onVisibilityChange,
}: {
    kind: TriggerKind;
    targetLabel: string;
    visibility: string;
    onVisibilityChange: (value: string) => void;
}) {
    // A data change belongs to the table, not to a person: everyone's copy of
    // the same trigger would fire on the same row, so "one per person" buys
    // duplicate runs rather than personal scoping. The choice is not offered.
    if (kind === ScheduleType.DATASTORE) {
        return (
            <div className="space-y-1.5">
                <Label className="text-xs">Runs as</Label>
                <p className="text-xs leading-5 text-[var(--text-secondary)]">
                    On a table with row-level security, each run acts as the person that record belongs
                    to, so it sees exactly what they can. On any other table it runs as you. One trigger
                    covers the pod — a copy per person would only run the same change twice.
                </p>
            </div>
        );
    }

    const normalized = visibility.toUpperCase();
    // A trigger saved with some other visibility keeps it: neither option reads
    // as chosen, and nothing is sent unless the reader picks one. Quietly
    // rewriting it to POD would widen who can see it without saying so.
    const options: Array<{ value: string; label: string; description: string }> = [
        {
            value: 'POD',
            label: 'Once for the pod',
            description: `One trigger covering everyone. ${targetLabel} runs with your access.`,
        },
        {
            value: 'PERSONAL',
            label: 'One per person',
            description: 'Only yours — it never runs for anyone else. Each teammate has to add their own, which then runs as them.',
        },
    ];

    return (
        <div className="space-y-2">
            <Label className="text-xs">Runs as</Label>
            <p className="text-xs leading-5 text-[var(--text-secondary)]">
                Nobody is there to run it, so it borrows your identity — {targetLabel} reaches the same
                tables, files, and connected accounts you can.
            </p>
            <div className="grid gap-2 sm:grid-cols-2">
                {options.map((option) => {
                    const active = normalized === option.value;
                    return (
                        <button
                            key={option.value}
                            type="button"
                            onClick={() => onVisibilityChange(option.value)}
                            aria-pressed={active}
                            className={cn(
                                'resource-option-button custom-focus-ring flex flex-col items-start rounded-lg border p-3 text-left transition-gentle',
                                active ? 'resource-option-selected' : 'resource-option-hover border-transparent',
                            )}
                        >
                            <span className="flex items-center gap-2">
                                <span
                                    className={cn(
                                        'flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border',
                                        active
                                            ? 'border-[var(--text-primary)] bg-[var(--text-primary)]'
                                            : 'border-[var(--border-strong)]',
                                    )}
                                    aria-hidden
                                >
                                    {active ? <span className="h-1.5 w-1.5 rounded-full bg-[var(--surface-1)]" /> : null}
                                </span>
                                <span className="text-sm font-medium text-[var(--text-primary)]">{option.label}</span>
                            </span>
                            <span className="mt-1.5 text-xs leading-5 text-[var(--text-secondary)]">
                                {option.description}
                            </span>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}

function TimeFields({
    cadence,
    onCadenceChange,
    timeOfDay,
    onTimeOfDayChange,
    weeklyDays,
    onWeeklyDaysChange,
    monthDay,
    onMonthDayChange,
    customCron,
    onCustomCronChange,
    timezone,
    onTimezoneChange,
    cron,
}: {
    cadence: TimeCadence;
    onCadenceChange: (value: TimeCadence) => void;
    timeOfDay: string;
    onTimeOfDayChange: (value: string) => void;
    weeklyDays: string[];
    onWeeklyDaysChange: (updater: (current: string[]) => string[]) => void;
    monthDay: number;
    onMonthDayChange: (value: number) => void;
    customCron: string;
    onCustomCronChange: (value: string) => void;
    timezone: string;
    onTimezoneChange: (value: string) => void;
    cron: string;
}) {
    return (
        <div className="space-y-3">
            <div className="flex flex-wrap gap-1.5">
                {CADENCES.map((option) => (
                    <button
                        key={option.value}
                        type="button"
                        onClick={() => onCadenceChange(option.value)}
                        className="choice-chip choice-chip-sm"
                        data-active={cadence === option.value ? 'true' : undefined}
                    >
                        {option.label}
                    </button>
                ))}
            </div>

            {cadence === 'weekly' ? (
                <div className="flex flex-wrap gap-1.5">
                    {WEEKDAY_OPTIONS.map((day) => (
                        <button
                            key={day.value}
                            type="button"
                            onClick={() => onWeeklyDaysChange((current) =>
                                current.includes(day.value)
                                    ? current.filter((value) => value !== day.value)
                                    : [...current, day.value],
                            )}
                            className="choice-chip choice-chip-xs"
                            data-active={weeklyDays.includes(day.value) ? 'true' : undefined}
                            aria-pressed={weeklyDays.includes(day.value)}
                        >
                            {day.label}
                        </button>
                    ))}
                </div>
            ) : null}

            <div className="grid gap-3 sm:grid-cols-2">
                {cadence !== 'hourly' && cadence !== 'custom' ? (
                    <div className="space-y-1.5">
                        <Label className="text-xs">Time</Label>
                        <Input type="time" value={timeOfDay} onChange={(event) => onTimeOfDayChange(event.target.value)} />
                    </div>
                ) : null}
                {cadence === 'monthly' ? (
                    <div className="space-y-1.5">
                        <Label className="text-xs">Day of month</Label>
                        <Input
                            type="number"
                            min={1}
                            max={31}
                            value={monthDay}
                            onChange={(event) => onMonthDayChange(Math.min(31, Math.max(1, Number(event.target.value) || 1)))}
                        />
                    </div>
                ) : null}
                {cadence === 'custom' ? (
                    <div className="space-y-1.5 sm:col-span-2">
                        <Label className="text-xs">Cron expression</Label>
                        <Input value={customCron} onChange={(event) => onCustomCronChange(event.target.value)} placeholder="0 9 * * 1-5" />
                    </div>
                ) : null}
                <div className="space-y-1.5">
                    <Label className="text-xs">Timezone</Label>
                    <Select value={timezone} onValueChange={onTimezoneChange}>
                        <SelectTrigger>
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            {TIMEZONES.map((zone) => (
                                <SelectItem key={zone} value={zone}>{zone}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
            </div>

            <p className="text-xs text-[var(--text-tertiary)]">
                {describeCron(cron)}{timezone ? ` · ${timezone}` : ''}
            </p>
        </div>
    );
}

function DataFields({
    tables,
    tableName,
    onTableChange,
    operations,
    onOperationsChange,
}: {
    tables: string[];
    tableName: string;
    onTableChange: (value: string) => void;
    operations: DataOperation[];
    onOperationsChange: (updater: (current: DataOperation[]) => DataOperation[]) => void;
}) {
    return (
        <div className="space-y-3">
            <div className="space-y-1.5">
                <Label className="text-xs">Table</Label>
                <Select value={tableName} onValueChange={onTableChange}>
                    <SelectTrigger>
                        <SelectValue placeholder={tables.length ? 'Choose table' : 'No tables available'} />
                    </SelectTrigger>
                    <SelectContent>
                        {tables.map((name) => (
                            <SelectItem key={name} value={name}>{name}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
            <div className="space-y-1.5">
                <Label className="text-xs">When rows are</Label>
                <div className="flex flex-wrap gap-1.5">
                    {DATA_OPERATION_OPTIONS.map((operation) => {
                        const selected = operations.includes(operation.value);
                        return (
                            <button
                                key={operation.value}
                                type="button"
                                className="choice-chip choice-chip-xs"
                                data-active={selected ? 'true' : undefined}
                                aria-pressed={selected}
                                onClick={() => onOperationsChange((current) => {
                                    if (selected) {
                                        const next = current.filter((value) => value !== operation.value);
                                        // One change type has to remain, or the
                                        // trigger listens for nothing.
                                        return next.length ? next : current;
                                    }
                                    return [...current, operation.value];
                                })}
                            >
                                {operation.label}
                            </button>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}

function EventFields({
    targetKind,
    isEditing,
    connectors,
    connectorId,
    onConnectorChange,
    triggers,
    triggerId,
    onTriggerChange,
    accounts,
    accountId,
    onAccountChange,
    workflowEvent,
    workflowEventBlocked,
}: {
    targetKind: TriggerTargetKind;
    isEditing: boolean;
    connectors: Array<{ id: string; label: string }>;
    connectorId: string;
    onConnectorChange: (value: string) => void;
    triggers: Array<{ id: string; label: string }>;
    triggerId: string;
    onTriggerChange: (value: string) => void;
    accounts: Account[];
    accountId: string;
    onAccountChange: (value: string) => void;
    workflowEvent: { connector_id?: string; connector_trigger_id?: string } | null;
    workflowEventBlocked: boolean;
}) {
    // Which app and event a webhook listens to is fixed when it is created —
    // the update API cannot move it — so editing states them rather than
    // offering fields that would not save.
    if (isEditing) {
        return (
            <div className="resource-soft-block p-3">
                <p className="text-sm text-[var(--text-primary)]">
                    {workflowEvent?.connector_trigger_id || triggerId || 'App event'}
                    {workflowEvent?.connector_id || connectorId ? ` · ${workflowEvent?.connector_id || connectorId}` : ''}
                </p>
                <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                    The app and event are set when a trigger is created. To listen to something else, delete this trigger and add a new one.
                </p>
            </div>
        );
    }

    if (targetKind === 'workflow') {
        return (
            <div className="space-y-3">
                <div className="resource-soft-block p-3">
                    <p className="text-sm text-[var(--text-primary)]">
                        {workflowEventBlocked
                            ? 'This workflow has no app-event start.'
                            : `${workflowEvent?.connector_trigger_id} · ${workflowEvent?.connector_id}`}
                    </p>
                    <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                        {workflowEventBlocked
                            ? 'Set the start to an app event on the Edit canvas, then add this trigger.'
                            : 'A workflow listens to the event named by its own start.'}
                    </p>
                </div>
                {workflowEventBlocked ? null : (
                    <AccountField accounts={accounts} accountId={accountId} onAccountChange={onAccountChange} />
                )}
            </div>
        );
    }

    return (
        <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                    <Label className="text-xs">App</Label>
                    <Select value={connectorId} onValueChange={onConnectorChange}>
                        <SelectTrigger>
                            <SelectValue placeholder="Choose app" />
                        </SelectTrigger>
                        <SelectContent>
                            {connectors.map((connector) => (
                                <SelectItem key={connector.id} value={connector.id}>{connector.label}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
                <div className="space-y-1.5">
                    <Label className="text-xs">Event</Label>
                    <Select value={triggerId} onValueChange={onTriggerChange} disabled={!connectorId}>
                        <SelectTrigger>
                            <SelectValue placeholder={connectorId ? 'Choose event' : 'Choose an app first'} />
                        </SelectTrigger>
                        <SelectContent>
                            {triggers.map((entry) => (
                                <SelectItem key={entry.id} value={entry.id}>{entry.label}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
            </div>
            <AccountField accounts={accounts} accountId={accountId} onAccountChange={onAccountChange} />
        </div>
    );
}

function AccountField({
    accounts,
    accountId,
    onAccountChange,
}: {
    accounts: Account[];
    accountId: string;
    onAccountChange: (value: string) => void;
}) {
    return (
        <div className="space-y-1.5">
            <Label className="text-xs">Connected account</Label>
            <Select value={accountId} onValueChange={onAccountChange} disabled={accounts.length === 0}>
                <SelectTrigger>
                    <SelectValue placeholder={accounts.length ? 'Choose account' : 'No connected account'} />
                </SelectTrigger>
                <SelectContent>
                    {accounts.map((account) => (
                        <SelectItem key={account.id} value={account.id}>{getAccountLabel(account)}</SelectItem>
                    ))}
                </SelectContent>
            </Select>
            {accounts.length === 0 ? (
                <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                    Connect this app under Connectors first.
                </p>
            ) : null}
        </div>
    );
}
