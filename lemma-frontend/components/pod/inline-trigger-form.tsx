'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Loader2, Sparkles } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useCreateSchedule } from '@/lib/hooks/use-schedules';
import { useTables } from '@/lib/hooks/use-datastores';
import { buildCronExpression, describeCron, type TimeCadence } from '@/lib/utils/schedules';
import { ScheduleType, type CreateScheduleRequest } from '@/lib/types';

export type TriggerTarget = { kind: 'agent' | 'workflow'; name: string };

type TriggerKind = 'time' | 'data';
type DataOperation = 'INSERT' | 'UPDATE' | 'DELETE';

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

const DATA_OPERATIONS: Array<{ value: DataOperation; label: string }> = [
    { value: 'INSERT', label: 'Created' },
    { value: 'UPDATE', label: 'Updated' },
    { value: 'DELETE', label: 'Deleted' },
];

// A compact, on-card schedule builder scoped to one agent/workflow. Covers the
// two trigger types that need no connector setup (time + data change) inline;
// app-event triggers fall through to the full editor via `moreOptionsHref`.
export function InlineTriggerForm({
    podId,
    target,
    moreOptionsHref,
    onCreated,
    onCancel,
}: {
    podId: string;
    target: TriggerTarget;
    moreOptionsHref: string;
    onCreated: () => void;
    onCancel: () => void;
}) {
    const createSchedule = useCreateSchedule(podId);
    const { data: tablesData } = useTables(podId);
    const tables = tablesData?.items || [];

    const [kind, setKind] = useState<TriggerKind>('time');
    const [cadence, setCadence] = useState<TimeCadence>('weekdays');
    const [timeOfDay, setTimeOfDay] = useState('09:00');
    const [weeklyDays, setWeeklyDays] = useState<string[]>(['1']);
    const [monthDay, setMonthDay] = useState(1);
    const [customCron, setCustomCron] = useState('0 9 * * 1-5');
    const [timezone, setTimezone] = useState('UTC');
    const [tableName, setTableName] = useState('');
    const [dataOperations, setDataOperations] = useState<DataOperation[]>(['INSERT']);

    const cron = buildCronExpression({ cadence, timeOfDay, weeklyDays, monthDay, customCron });
    const selectedTable = tableName || tables[0]?.name || '';

    const handleCreate = async () => {
        let config: Record<string, unknown>;
        if (kind === 'time') {
            if (!cron.trim()) {
                toast.error('Add a cron expression.');
                return;
            }
            config = { schedule_type: 'CRON', cron_expression: cron.trim(), timezone: timezone.trim() || 'UTC' };
        } else {
            if (!selectedTable) {
                toast.error('Choose a table for this trigger.');
                return;
            }
            if (dataOperations.length === 0) {
                toast.error('Choose at least one change type.');
                return;
            }
            config = { table_name: selectedTable, operations: dataOperations };
        }

        const payload: CreateScheduleRequest = {
            schedule_type: kind === 'time' ? ScheduleType.TIME : ScheduleType.DATASTORE,
            workflow_name: target.kind === 'workflow' ? target.name : null,
            agent_name: target.kind === 'agent' ? target.name : null,
            account_id: null,
            connector_trigger_id: null,
            config,
            filter_instruction: null,
            filter_output_schema: null,
            visibility: 'POD' as never,
        };

        try {
            await createSchedule.mutateAsync(payload);
            toast.success('Trigger created');
            onCreated();
        } catch (error) {
            toast.error(error instanceof Error ? error.message : 'Failed to create trigger');
        }
    };

    return (
        <>
            <div className="mb-3 flex items-center gap-2">
                {(['time', 'data'] as TriggerKind[]).map((option) => (
                    <button
                        key={option}
                        type="button"
                        onClick={() => setKind(option)}
                        className="choice-chip choice-chip-sm"
                        data-active={kind === option ? 'true' : undefined}
                    >
                        {option === 'time' ? 'On a schedule' : 'On data change'}
                    </button>
                ))}
            </div>

            {kind === 'time' ? (
                <div className="space-y-3">
                    <div className="flex flex-wrap gap-1.5">
                        {CADENCES.map((option) => (
                            <button
                                key={option.value}
                                type="button"
                                onClick={() => setCadence(option.value)}
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
                                    onClick={() => setWeeklyDays((current) =>
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

                    <div className="grid gap-2 sm:grid-cols-2">
                        {cadence !== 'hourly' && cadence !== 'custom' ? (
                            <div className="space-y-1">
                                <Label className="text-xs">Time</Label>
                                <Input type="time" value={timeOfDay} onChange={(event) => setTimeOfDay(event.target.value)} />
                            </div>
                        ) : null}
                        {cadence === 'monthly' ? (
                            <div className="space-y-1">
                                <Label className="text-xs">Day of month</Label>
                                <Input
                                    type="number"
                                    min={1}
                                    max={31}
                                    value={monthDay}
                                    onChange={(event) => setMonthDay(Math.min(31, Math.max(1, Number(event.target.value) || 1)))}
                                />
                            </div>
                        ) : null}
                        {cadence === 'custom' ? (
                            <div className="space-y-1 sm:col-span-2">
                                <Label className="text-xs">Cron expression</Label>
                                <Input value={customCron} onChange={(event) => setCustomCron(event.target.value)} placeholder="0 9 * * 1-5" />
                            </div>
                        ) : null}
                        <div className="space-y-1">
                            <Label className="text-xs">Timezone</Label>
                            <Select value={timezone} onValueChange={setTimezone}>
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

                    <p className="text-xs text-[var(--text-tertiary)]">{describeCron(cron)}{timezone ? ` · ${timezone}` : ''}</p>
                </div>
            ) : (
                <div className="space-y-3">
                    <div className="space-y-1">
                        <Label className="text-xs">Table</Label>
                        <Select value={selectedTable} onValueChange={setTableName}>
                            <SelectTrigger>
                                <SelectValue placeholder={tables.length ? 'Choose table' : 'No tables available'} />
                            </SelectTrigger>
                            <SelectContent>
                                {tables.map((table) => (
                                    <SelectItem key={table.name} value={table.name}>{table.name}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="space-y-1">
                        <Label className="text-xs">When rows are</Label>
                        <div className="flex flex-wrap gap-1.5">
                            {DATA_OPERATIONS.map((operation) => {
                                const selected = dataOperations.includes(operation.value);
                                return (
                                    <button
                                        key={operation.value}
                                        type="button"
                                        className="choice-chip choice-chip-xs"
                                        data-active={selected ? 'true' : undefined}
                                        onClick={() => setDataOperations((current) => {
                                            if (selected) {
                                                const next = current.filter((value) => value !== operation.value);
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
            )}

            <div className="mt-3 flex items-center justify-between gap-2">
                <Link href={moreOptionsHref} className="text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
                    More options
                </Link>
                <div className="flex items-center gap-2">
                    <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={createSchedule.isPending}>
                        Cancel
                    </Button>
                    <Button type="button" size="sm" className="gap-1.5" onClick={() => void handleCreate()} disabled={createSchedule.isPending}>
                        {createSchedule.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                        Create
                    </Button>
                </div>
            </div>
        </>
    );
}
