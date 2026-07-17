'use client';

import { use, useMemo, useState, type ReactNode } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowLeft, ArrowRight, CalendarClock, CheckCircle2, Database, Loader2, Sparkles, Webhook } from '@/components/ui/icons';
import { toast } from 'sonner';

import { ProductIcon } from '@/components/pod/product-icon';
import { PodPageHeader } from '@/components/pod/pod-page-header';
import { EmptyState } from '@/components/shared/empty-state';
import { showResourceCreatedToast, showResourceErrorToast } from '@/components/shared/resource-feedback';
import { ResourceVisibilityBadge, ResourceVisibilitySelect } from '@/components/shared/resource-visibility';
import { ConceptHint } from '@/components/education/concept-hint';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useAgents } from '@/lib/hooks/use-agents';
import { useTables } from '@/lib/hooks/use-datastores';
import { useFlows } from '@/lib/hooks/use-flows';
import { useAccounts, useConnectors, useTriggers } from '@/lib/hooks/use-connectors';
import { useCreateSchedule } from '@/lib/hooks/use-schedules';
import { usePod } from '@/lib/hooks/use-pods';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { cn } from '@/lib/utils';
import { describeCron } from '@/lib/utils/schedules';
import { ScheduleType, type Account, type CreateScheduleRequest, type Workflow as LemmaWorkflow } from '@/lib/types';

type TargetKind = 'workflow' | 'agent';
type ScheduleTypeValue = `${ScheduleType}`;
type TimeCadence = 'hourly' | 'daily' | 'weekdays' | 'weekly' | 'monthly' | 'custom';
type DataOperation = 'INSERT' | 'UPDATE' | 'DELETE';

// Cron day-of-week values (0 = Sunday … 6 = Saturday).
const WEEKDAY_OPTIONS = [
    { value: '1', label: 'Mon' },
    { value: '2', label: 'Tue' },
    { value: '3', label: 'Wed' },
    { value: '4', label: 'Thu' },
    { value: '5', label: 'Fri' },
    { value: '6', label: 'Sat' },
    { value: '0', label: 'Sun' },
] as const;
type ScheduleCreateStep = 'worker' | 'trigger' | 'condition' | 'review';

const SCHEDULE_CREATE_STEPS: Array<{
    id: ScheduleCreateStep;
    label: string;
    eyebrow: string;
    description: string;
}> = [
    {
        id: 'worker',
        label: 'Choose what runs',
        eyebrow: 'Work',
        description: 'Pick the workflow or agent this schedule should wake up.',
    },
    {
        id: 'trigger',
        label: 'Choose when it starts',
        eyebrow: 'Trigger',
        description: 'Decide the rhythm or event that should start the work.',
    },
    {
        id: 'condition',
        label: 'Add a guardrail',
        eyebrow: 'Condition',
        description: 'Optionally tell the schedule when to stand down.',
    },
    {
        id: 'review',
        label: 'Review and create',
        eyebrow: 'Ready',
        description: 'Check the schedule before teaching it to the pod.',
    },
];

const SCHEDULE_TYPES: Array<{
    value: ScheduleTypeValue;
    label: string;
    description: string;
    icon: typeof CalendarClock;
}> = [
    {
        value: ScheduleType.TIME,
        label: 'Time rhythm',
        description: 'Run on a recurring cadence.',
        icon: CalendarClock,
    },
    {
        value: ScheduleType.WEBHOOK,
        label: 'App event',
        description: 'Run when an app sends a signal.',
        icon: Webhook,
    },
    {
        value: ScheduleType.DATASTORE,
        label: 'Data change',
        description: 'Run when table data changes.',
        icon: Database,
    },
];

const TIMEZONES = ['UTC', 'Asia/Kolkata', 'America/New_York', 'America/Los_Angeles', 'Europe/London'] as const;
const DATA_OPERATIONS: Array<{ value: DataOperation; label: string }> = [
    { value: 'INSERT', label: 'New records' },
    { value: 'UPDATE', label: 'Updates' },
    { value: 'DELETE', label: 'Deletes' },
];

function splitTime(value: string): { hour: string; minute: string } {
    const [hour = '09', minute = '00'] = value.split(':');
    return {
        hour: hour.padStart(2, '0'),
        minute: minute.padStart(2, '0'),
    };
}

function buildCronExpression({
    cadence,
    timeOfDay,
    weeklyDays,
    monthDay,
    customCron,
}: {
    cadence: TimeCadence;
    timeOfDay: string;
    weeklyDays: string[];
    monthDay: number;
    customCron: string;
}) {
    if (cadence === 'custom') return customCron.trim();
    if (cadence === 'hourly') return '0 * * * *';

    const { hour, minute } = splitTime(timeOfDay);
    if (cadence === 'daily') return `${minute} ${hour} * * *`;
    if (cadence === 'weekdays') return `${minute} ${hour} * * 1-5`;
    if (cadence === 'weekly') return `${minute} ${hour} * * ${weeklyDays.length ? weeklyDays.join(',') : '1'}`;
    return `${minute} ${hour} ${Math.min(31, Math.max(1, monthDay))} * *`;
}

function getEventConfig(workflow: LemmaWorkflow | undefined): { connector_id?: string; connector_trigger_id?: string } | null {
    if (workflow?.start?.type !== 'EVENT' || !workflow.start.config) return null;
    return workflow.start.config as { connector_id?: string; connector_trigger_id?: string };
}

function getTriggerLabel(trigger: { id: string; description?: string | null } & Record<string, unknown>): string {
    return String(trigger.name || trigger.title || trigger.event_type || trigger.description || trigger.id);
}

function getAccountLabel(account: Account): string {
    return account.display_name || account.email || account.id;
}

function formatTargetLabel(value: string) {
    return value || 'Untitled';
}

export default function NewSchedulePage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const router = useRouter();
    const searchParams = useSearchParams();
    const podAccess = usePodAccess(podId);
    const requestedWorkflow = searchParams.get('workflow') || '';
    const requestedAgent = searchParams.get('agent') || '';

    const { data: pod } = usePod(podId);
    const { data: flowsData } = useFlows(podId);
    const { data: agentsData } = useAgents(podId);
    const { data: tablesData } = useTables(podId);
    const { data: connectors = [] } = useConnectors({ limit: 100 });
    const { data: accounts = [] } = useAccounts({ organizationId: pod?.organization_id, limit: 100 });

    const workflows = useMemo(() => flowsData || [], [flowsData]);
    const agents = useMemo(() => agentsData?.items || [], [agentsData?.items]);
    const tables = useMemo(() => tablesData?.items || [], [tablesData?.items]);

    const [targetKind, setTargetKind] = useState<TargetKind>(requestedAgent ? 'agent' : 'workflow');
    const [targetName, setTargetName] = useState(requestedAgent || requestedWorkflow || '');
    const [scheduleType, setScheduleType] = useState<ScheduleTypeValue>(ScheduleType.TIME);
    const [timeCadence, setTimeCadence] = useState<TimeCadence>('weekdays');
    const [timeOfDay, setTimeOfDay] = useState('09:00');
    const [weeklyDays, setWeeklyDays] = useState<string[]>(['1']);
    const [monthDay, setMonthDay] = useState(1);
    const [customCron, setCustomCron] = useState('0 9 * * 1-5');
    const [timezone, setTimezone] = useState('UTC');
    const [dataTableName, setDataTableName] = useState('');
    const [dataOperations, setDataOperations] = useState<DataOperation[]>(['INSERT']);
    const [connectorId, setConnectorId] = useState('');
    const [triggerId, setTriggerId] = useState('');
    const [accountId, setAccountId] = useState('');
    const [filterInstruction, setFilterInstruction] = useState('');
    const [visibility, setVisibility] = useState('POD');
    const [createStep, setCreateStep] = useState<ScheduleCreateStep>('worker');

    const { data: triggers = [] } = useTriggers({
        organizationId: pod?.organization_id,
        connectorId,
        limit: 100,
        enabled: scheduleType === ScheduleType.WEBHOOK && !!connectorId,
    });

    const createSchedule = useCreateSchedule(podId);
    const defaultTargetName = targetKind === 'workflow' ? workflows[0]?.name || '' : agents[0]?.name || '';
    const selectedTargetName = targetName || defaultTargetName;
    const selectedDataTableName = dataTableName || tables[0]?.name || '';
    const selectedAgentTriggerId = targetKind === 'agent' && triggerId && triggers.some((trigger) => trigger.id === triggerId)
        ? triggerId
        : targetKind === 'agent'
            ? triggers[0]?.id || ''
            : '';
    const selectedWorkflow = useMemo(
        () => workflows.find((workflow) => workflow.name === selectedTargetName),
        [selectedTargetName, workflows]
    );
    const selectedWorkflowEventConfig = useMemo(
        () => (targetKind === 'workflow' && scheduleType === ScheduleType.WEBHOOK ? getEventConfig(selectedWorkflow) : null),
        [scheduleType, selectedWorkflow, targetKind]
    );
    const selectedEventConnectorId = selectedWorkflowEventConfig?.connector_id || (targetKind === 'agent' ? connectorId : '');

    const compatibleAccounts = useMemo(() => {
        if (!selectedEventConnectorId) return accounts;
        return accounts.filter((account) => account.connector_id === selectedEventConnectorId);
    }, [accounts, selectedEventConnectorId]);
    const selectedAccountId = scheduleType === ScheduleType.WEBHOOK && accountId && compatibleAccounts.some((account) => account.id === accountId)
        ? accountId
        : scheduleType === ScheduleType.WEBHOOK
            ? (compatibleAccounts.find((account) => account.is_default)?.id ?? compatibleAccounts[0]?.id ?? '')
            : '';

    const cronExpression = buildCronExpression({
        cadence: timeCadence,
        timeOfDay,
        weeklyDays,
        monthDay,
        customCron,
    });
    const timeCadenceDescription = describeCron(cronExpression);
    const timeDescription = timezone ? `${timeCadenceDescription} · ${timezone}` : timeCadenceDescription;
    const selectedConnectorLabel = connectors.find((app) => app.id === selectedEventConnectorId)?.title
        || connectors.find((app) => app.id === selectedEventConnectorId)?.name
        || selectedEventConnectorId;
    const selectedAgentTriggerLabel = triggers.find((trigger) => trigger.id === selectedAgentTriggerId)
        ? getTriggerLabel(triggers.find((trigger) => trigger.id === selectedAgentTriggerId) as { id: string; description?: string | null } & Record<string, unknown>)
        : selectedAgentTriggerId;
    const workerVerb = targetKind === 'workflow' ? 'Run workflow' : 'Run agent';
    const schedulePreview = scheduleType === ScheduleType.TIME
        ? `${workerVerb} ${formatTargetLabel(selectedTargetName)} ${timeDescription.toLowerCase()}.`
        : scheduleType === ScheduleType.DATASTORE
            ? `${workerVerb} ${formatTargetLabel(selectedTargetName)} when ${dataOperations.map((operation) => operation.toLowerCase()).join(', ')} happens in ${selectedDataTableName || 'a table'}.`
            : targetKind === 'workflow'
                ? `${workerVerb} ${formatTargetLabel(selectedTargetName)} from ${selectedConnectorLabel || 'an app'} events.`
                : `${workerVerb} ${formatTargetLabel(selectedTargetName)} when ${selectedAgentTriggerLabel || 'an app event'} happens.`;
    const currentCreateStepIndex = Math.max(0, SCHEDULE_CREATE_STEPS.findIndex((step) => step.id === createStep));
    const activeStep = SCHEDULE_CREATE_STEPS[currentCreateStepIndex];
    const triggerReady = scheduleType === ScheduleType.TIME
        ? Boolean(cronExpression.trim())
        : scheduleType === ScheduleType.DATASTORE
            ? Boolean(selectedDataTableName) && dataOperations.length > 0
            : targetKind === 'agent'
                ? Boolean(connectorId && selectedAgentTriggerId && selectedAccountId)
                : Boolean(selectedWorkflowEventConfig?.connector_id && selectedWorkflowEventConfig.connector_trigger_id && selectedAccountId);
    const canContinueCreate = createStep === 'worker'
        ? Boolean(selectedTargetName)
        : createStep === 'trigger'
            ? triggerReady
            : true;
    const stepDone: Record<ScheduleCreateStep, boolean> = {
        worker: Boolean(selectedTargetName),
        trigger: triggerReady,
        condition: createStep === 'review' || Boolean(filterInstruction.trim()),
        review: false,
    };

    const goToNextCreateStep = () => {
        if (!podAccess.can('schedule.create')) return;
        if (!canContinueCreate) return;
        const nextStep = SCHEDULE_CREATE_STEPS[Math.min(SCHEDULE_CREATE_STEPS.length - 1, currentCreateStepIndex + 1)];
        setCreateStep(nextStep.id);
    };

    const goToPreviousCreateStep = () => {
        if (!podAccess.can('schedule.create')) return;
        const previousStep = SCHEDULE_CREATE_STEPS[Math.max(0, currentCreateStepIndex - 1)];
        setCreateStep(previousStep.id);
    };

    const handleCreate = async () => {
        if (!podAccess.can('schedule.create')) return;
        if (!selectedTargetName) {
            toast.error('Choose a workflow or agent first.');
            return;
        }

        try {
            let config: Record<string, unknown> = {};
            if (scheduleType === ScheduleType.TIME) {
                if (!cronExpression.trim()) {
                    toast.error('Add a cron expression.');
                    return;
                }
                config = {
                    schedule_type: 'CRON',
                    cron_expression: cronExpression.trim(),
                    timezone: timezone.trim() || 'UTC',
                };
            } else if (scheduleType === ScheduleType.DATASTORE) {
                if (!selectedDataTableName) {
                    toast.error('Choose a table for this data schedule.');
                    return;
                }
                if (dataOperations.length === 0) {
                    toast.error('Choose at least one data operation.');
                    return;
                }
                config = {
                    table_name: selectedDataTableName,
                    operations: dataOperations,
                };
            } else if (targetKind === 'agent') {
                if (!connectorId) {
                    toast.error('Choose an app for this event schedule.');
                    return;
                }
                if (!selectedAgentTriggerId) {
                    toast.error('Choose the event that should start this agent schedule.');
                    return;
                }
                if (!selectedAccountId) {
                    toast.error('Choose the connected account for this event schedule.');
                    return;
                }
                config = {
                    connector_id: connectorId,
                    connector_trigger_id: selectedAgentTriggerId,
                    trigger_config: {},
                };
            } else {
                if (!selectedWorkflowEventConfig?.connector_id || !selectedWorkflowEventConfig.connector_trigger_id) {
                    toast.error('Choose a workflow that starts from an app event.');
                    return;
                }
                if (!selectedAccountId) {
                    toast.error('Choose the connected account for this workflow event schedule.');
                    return;
                }
                config = {};
            }

            const payload: CreateScheduleRequest = {
                schedule_type: scheduleType as ScheduleType,
                workflow_name: targetKind === 'workflow' ? selectedTargetName : null,
                agent_name: targetKind === 'agent' ? selectedTargetName : null,
                account_id: scheduleType === ScheduleType.WEBHOOK ? (selectedAccountId || null) : null,
                connector_trigger_id: scheduleType === ScheduleType.WEBHOOK && targetKind === 'agent' ? selectedAgentTriggerId : null,
                config,
                filter_instruction: filterInstruction.trim() || null,
                filter_output_schema: null,
                visibility: visibility as never,
            };

            await createSchedule.mutateAsync(payload);
            showResourceCreatedToast('Schedule', selectedTargetName);
            router.push(`/pod/${podId}/schedules`);
        } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to create schedule';
            showResourceErrorToast(error, message);
        }
    };

    if (!podAccess.isLoading && !podAccess.can('schedule.create')) {
        return (
            <div className="context-shell flex min-h-full items-center justify-center bg-transparent p-6">
                <EmptyState
                    variant="panel"
                    icon={<CalendarClock className="h-5 w-5" />}
                    title="No access to create schedules"
                    description="You can still open schedules you have permission to read."
                />
            </div>
        );
    }

    return (
        <div className="agent-builder-root flex h-full min-h-0 flex-col">
            <PodPageHeader
                podId={podId}
                variant="bar"
                title="Create schedule"
                eyebrow="Guided builder"
                backHref={`/pod/${podId}/schedules`}
                backLabel="Schedules"
                productIconKind="schedules"
                meta={(
                    <span className="text-xs text-[var(--text-secondary)]">
                        {selectedTargetName ? 'Ready' : 'Draft'} · {scheduleType.toLowerCase()}
                    </span>
                )}
            />

            <main className="min-h-0 flex-1 overflow-y-auto">
                <div className="agent-builder-canvas mx-auto w-full max-w-[76rem] px-6 pb-24 pt-6">
                    <section className="agent-builder-hero">
                        <div className="min-w-0">
                            <p className="section-label">{activeStep.eyebrow}</p>
                            <h1 className="agent-builder-title mt-2 flex items-center gap-2">
                                {activeStep.label}
                                <ConceptHint concept="schedule" />
                            </h1>
                            <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--text-secondary)]">{activeStep.description}</p>
                        </div>
                        <div className="hidden text-right text-sm text-[var(--text-secondary)] sm:block">
                            <span className="type-eyebrow">Draft</span>
                            <span className="ml-2 text-[var(--text-primary)]">{selectedTargetName || 'No target yet'}</span>
                        </div>
                    </section>

                    <ScheduleCreateProgress currentStep={createStep} stepDone={stepDone} onSelect={setCreateStep} />

                    <section className="agent-builder-stage" data-step={createStep}>
                        <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
                            <div className="min-w-0">
                                {createStep === 'worker' ? (
                                    <div className="space-y-4">
                                        <div className="grid gap-3 sm:grid-cols-2">
                                            <ScheduleChoiceCard
                                                active={targetKind === 'workflow'}
                                                icon={<ProductIcon kind="workflows" size="lg" />}
                                                title="Workflow"
                                                description="Run a repeatable procedure."
                                                onClick={() => {
                                                    setTargetKind('workflow');
                                                    setTargetName(workflows[0]?.name || '');
                                                }}
                                            />
                                            <ScheduleChoiceCard
                                                active={targetKind === 'agent'}
                                                icon={<ProductIcon kind="agents" size="lg" />}
                                                title="Agent"
                                                description="Wake up an AI worker."
                                                onClick={() => {
                                                    setTargetKind('agent');
                                                    setTargetName(agents[0]?.name || '');
                                                }}
                                            />
                                        </div>

                                        <div className="space-y-1.5">
                                            <Label>{targetKind === 'workflow' ? 'Which workflow?' : 'Which agent?'}</Label>
                                            <Select value={selectedTargetName} onValueChange={setTargetName}>
                                                <SelectTrigger>
                                                    <SelectValue placeholder={`Choose ${targetKind === 'workflow' ? 'workflow' : 'agent'}`} />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {(targetKind === 'workflow' ? workflows : agents).map((item) => (
                                                        <SelectItem key={item.name} value={item.name}>
                                                            {item.name}
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>
                                ) : null}

                                {createStep === 'trigger' ? (
                                    <div className="space-y-4">
                                        <div className="grid gap-3 sm:grid-cols-3">
                                            {SCHEDULE_TYPES.map((option) => (
                                                <ScheduleChoiceCard
                                                    key={option.value}
                                                    active={scheduleType === option.value}
                                                    icon={option.value === ScheduleType.TIME
                                                        ? <ProductIcon kind="schedules" size="lg" />
                                                        : option.value === ScheduleType.DATASTORE
                                                            ? <ProductIcon kind="data" size="lg" />
                                                            : <ProductIcon kind="surfaces" size="lg" />}
                                                    title={option.label}
                                                    description={option.description}
                                                    onClick={() => setScheduleType(option.value)}
                                                />
                                            ))}
                                        </div>

                                        {scheduleType === ScheduleType.TIME ? (
                                            <div className="space-y-3">
                                                <div className="flex flex-wrap gap-2">
                                                    {[
                                                        { value: 'hourly', label: 'Hourly' },
                                                        { value: 'daily', label: 'Daily' },
                                                        { value: 'weekdays', label: 'Weekdays' },
                                                        { value: 'weekly', label: 'Weekly' },
                                                        { value: 'monthly', label: 'Monthly' },
                                                        { value: 'custom', label: 'Custom' },
                                                    ].map((option) => (
                                                        <button
                                                            key={option.value}
                                                            type="button"
                                                            onClick={() => setTimeCadence(option.value as TimeCadence)}
                                                            className="choice-chip choice-chip-sm"
                                                            data-active={timeCadence === option.value ? 'true' : undefined}
                                                        >
                                                            {option.label}
                                                        </button>
                                                    ))}
                                                </div>

                                                <div className="grid gap-3 sm:grid-cols-2">
                                                    {timeCadence !== 'hourly' && timeCadence !== 'custom' ? (
                                                        <div className="space-y-1.5">
                                                            <Label>Time</Label>
                                                            <Input type="time" value={timeOfDay} onChange={(event) => setTimeOfDay(event.target.value)} />
                                                        </div>
                                                    ) : null}
                                                    {timeCadence === 'weekly' ? (
                                                        <div className="space-y-1.5 sm:col-span-2">
                                                            <Label>Days of week</Label>
                                                            <div className="flex flex-wrap gap-2">
                                                                {WEEKDAY_OPTIONS.map((day) => (
                                                                    <button
                                                                        key={day.value}
                                                                        type="button"
                                                                        onClick={() =>
                                                                            setWeeklyDays((current) =>
                                                                                current.includes(day.value)
                                                                                    ? current.filter((value) => value !== day.value)
                                                                                    : [...current, day.value],
                                                                            )
                                                                        }
                                                                        className="choice-chip choice-chip-sm"
                                                                        data-active={weeklyDays.includes(day.value) ? 'true' : undefined}
                                                                        aria-pressed={weeklyDays.includes(day.value)}
                                                                    >
                                                                        {day.label}
                                                                    </button>
                                                                ))}
                                                            </div>
                                                        </div>
                                                    ) : null}
                                                    {timeCadence === 'monthly' ? (
                                                        <div className="space-y-1.5">
                                                            <Label>Day of month</Label>
                                                            <Input
                                                                type="number"
                                                                min={1}
                                                                max={31}
                                                                value={monthDay}
                                                                onChange={(event) =>
                                                                    setMonthDay(Math.min(31, Math.max(1, Number(event.target.value) || 1)))
                                                                }
                                                            />
                                                        </div>
                                                    ) : null}
                                                    {timeCadence === 'custom' ? (
                                                        <div className="space-y-1.5 sm:col-span-2">
                                                            <Label>Cron expression</Label>
                                                            <Input value={customCron} onChange={(event) => setCustomCron(event.target.value)} placeholder="0 9 * * 1-5" />
                                                        </div>
                                                    ) : null}
                                                    <div className="space-y-1.5">
                                                        <Label>Timezone</Label>
                                                        <Select value={timezone} onValueChange={setTimezone}>
                                                            <SelectTrigger>
                                                                <SelectValue />
                                                            </SelectTrigger>
                                                            <SelectContent>
                                                                {TIMEZONES.map((zone) => (
                                                                    <SelectItem key={zone} value={zone}>
                                                                        {zone}
                                                                    </SelectItem>
                                                                ))}
                                                            </SelectContent>
                                                        </Select>
                                                    </div>
                                                </div>
                                            </div>
                                        ) : scheduleType === ScheduleType.DATASTORE ? (
                                            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
                                                <div className="space-y-1.5">
                                                    <Label>Table</Label>
                                                    <Select value={selectedDataTableName} onValueChange={setDataTableName}>
                                                        <SelectTrigger>
                                                            <SelectValue placeholder={tables.length ? 'Choose table' : 'No tables available'} />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            {tables.map((table) => (
                                                                <SelectItem key={table.name} value={table.name}>
                                                                    {table.name}
                                                                </SelectItem>
                                                            ))}
                                                        </SelectContent>
                                                    </Select>
                                                </div>
                                                <div className="space-y-1.5">
                                                    <Label>When rows are</Label>
                                                    <div className="flex flex-wrap gap-2">
                                                        {DATA_OPERATIONS.map((operation) => {
                                                            const selected = dataOperations.includes(operation.value);
                                                            return (
                                                                <button
                                                                    key={operation.value}
                                                                    type="button"
                                                                    className="schedule-operation-chip choice-chip choice-chip-xs"
                                                                    data-active={selected ? 'true' : undefined}
                                                                    onClick={() => {
                                                                        setDataOperations((current) => {
                                                                            if (selected) {
                                                                                const next = current.filter((value) => value !== operation.value);
                                                                                return next.length ? next : current;
                                                                            }
                                                                            return [...current, operation.value];
                                                                        });
                                                                    }}
                                                                >
                                                                    {operation.label}
                                                                </button>
                                                            );
                                                        })}
                                                    </div>
                                                </div>
                                            </div>
                                        ) : targetKind === 'workflow' ? (
                                            <div className="space-y-3">
                                                <div className="resource-soft-block p-3">
                                                    <p className="text-sm font-normal text-[var(--text-primary)]">
                                                        {selectedWorkflowEventConfig ? 'This workflow already knows its event.' : 'This workflow has no event start.'}
                                                    </p>
                                                    <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                                                        {selectedWorkflowEventConfig
                                                            ? `${selectedWorkflowEventConfig.connector_id || 'App'} · ${selectedWorkflowEventConfig.connector_trigger_id || 'Event'}`
                                                            : 'Choose a workflow whose start type is Event, or use a Time/Data schedule.'}
                                                    </p>
                                                </div>
                                                <div className="space-y-1.5">
                                                    <Label>Connected account</Label>
                                                    <Select value={selectedAccountId} onValueChange={setAccountId} disabled={!selectedEventConnectorId || compatibleAccounts.length === 0}>
                                                        <SelectTrigger>
                                                            <SelectValue placeholder={compatibleAccounts.length ? 'Choose account' : 'No connected account'} />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            {compatibleAccounts.map((account) => (
                                                                <SelectItem key={account.id} value={account.id}>
                                                                    {getAccountLabel(account)}
                                                                </SelectItem>
                                                            ))}
                                                        </SelectContent>
                                                    </Select>
                                                </div>
                                            </div>
                                        ) : (
                                            <div className="grid gap-3 sm:grid-cols-3">
                                                <div className="space-y-1.5">
                                                    <Label>App</Label>
                                                    <Select value={connectorId} onValueChange={(value) => {
                                                        setConnectorId(value);
                                                        setTriggerId('');
                                                    }}>
                                                        <SelectTrigger>
                                                            <SelectValue placeholder="Choose app" />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            {connectors.map((app) => (
                                                                <SelectItem key={app.id} value={app.id}>
                                                                    {app.title || app.name || app.id}
                                                                </SelectItem>
                                                            ))}
                                                        </SelectContent>
                                                    </Select>
                                                </div>
                                                <div className="space-y-1.5">
                                                    <Label>Event</Label>
                                                    <Select value={selectedAgentTriggerId} onValueChange={setTriggerId} disabled={!connectorId}>
                                                        <SelectTrigger>
                                                            <SelectValue placeholder="Choose event" />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            {triggers.map((trigger) => (
                                                                <SelectItem key={trigger.id} value={trigger.id}>
                                                                    {getTriggerLabel(trigger as { id: string; description?: string | null } & Record<string, unknown>)}
                                                                </SelectItem>
                                                            ))}
                                                        </SelectContent>
                                                    </Select>
                                                </div>
                                                <div className="space-y-1.5">
                                                    <Label>Account</Label>
                                                    <Select value={selectedAccountId} onValueChange={setAccountId} disabled={!connectorId || compatibleAccounts.length === 0}>
                                                        <SelectTrigger>
                                                            <SelectValue placeholder={compatibleAccounts.length ? 'Choose account' : 'No account'} />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            {compatibleAccounts.map((account) => (
                                                                <SelectItem key={account.id} value={account.id}>
                                                                    {getAccountLabel(account)}
                                                                </SelectItem>
                                                            ))}
                                                        </SelectContent>
                                                    </Select>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                ) : null}

                                {createStep === 'condition' ? (
                                    <div className="space-y-2">
                                        <Label>Only run when...</Label>
                                        <Textarea
                                            value={filterInstruction}
                                            onChange={(event) => setFilterInstruction(event.target.value)}
                                            placeholder="Example: only run if the record is high priority and has an owner."
                                            className="min-h-36 resize-y"
                                        />
                                        <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                                            Leave this blank if the schedule should run every time the trigger happens.
                                        </p>
                                    </div>
                                ) : null}

                                {createStep === 'review' ? (
                                    <div className="resource-option-selected rounded-lg p-4">
                                        <p className="text-sm font-normal text-[var(--text-primary)]">Ready to create</p>
                                        <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{schedulePreview}</p>
                                        {filterInstruction.trim() ? (
                                            <p className="mt-3 rounded-md bg-[var(--row-bg)] px-3 py-2 text-xs leading-5 text-[var(--text-secondary)]">
                                                Only run when: {filterInstruction.trim()}
                                            </p>
                                        ) : null}
                                        <div className="mt-4">
                                            <ResourceVisibilityBadge visibility={visibility} resourceLabel="schedules" />
                                        </div>
                                    </div>
                                ) : null}
                            </div>

                            <ScheduleCreatePreview
                                targetKind={targetKind}
                                targetName={selectedTargetName}
                                scheduleType={scheduleType}
                                preview={schedulePreview}
                                filterInstruction={filterInstruction}
                                timeDescription={timeDescription}
                                visibility={visibility}
                                onVisibilityChange={setVisibility}
                            />
                        </div>
                    </section>
                </div>
            </main>

            <footer className="resource-footer-glass shrink-0 px-5 py-2.5 backdrop-blur-md">
                <div className="mx-auto flex w-full max-w-[76rem] items-center justify-between gap-3">
                    <div className="min-w-0">
                        <p className="type-eyebrow">
                            Step {currentCreateStepIndex + 1} of {SCHEDULE_CREATE_STEPS.length}
                        </p>
                        <p className="truncate text-sm font-normal text-[var(--text-primary)]">
                            {activeStep.label}
                            {createStep !== 'review' ? <span className="font-normal text-[var(--text-tertiary)]"> · Next: {SCHEDULE_CREATE_STEPS[currentCreateStepIndex + 1]?.label}</span> : null}
                        </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                        <Button type="button" variant="outline" onClick={goToPreviousCreateStep} disabled={createStep === 'worker' || createSchedule.isPending} className="gap-2">
                            <ArrowLeft className="h-3.5 w-3.5" />
                            Previous
                        </Button>
                        {createStep === 'review' ? (
                            <Button type="button" onClick={() => void handleCreate()} disabled={createSchedule.isPending || !selectedTargetName} className="gap-2">
                                {createSchedule.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                                {createSchedule.isPending ? 'Creating...' : 'Create schedule'}
                            </Button>
                        ) : (
                            <Button type="button" onClick={goToNextCreateStep} disabled={!canContinueCreate || createSchedule.isPending} className="gap-2">
                                Next
                                <ArrowRight className="h-3.5 w-3.5" />
                            </Button>
                        )}
                    </div>
                </div>
            </footer>
        </div>
    );
}

function ScheduleChoiceCard({
    active,
    icon,
    title,
    description,
    onClick,
}: {
    active: boolean;
    icon: ReactNode;
    title: string;
    description: string;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={cn(
                'schedule-choice-card-button custom-focus-ring flex min-h-24 flex-col items-start rounded-lg border p-3 text-left transition-gentle',
                active
                    ? 'resource-option-selected'
                    : 'resource-option-hover border-transparent bg-transparent'
            )}
        >
            <span className="flex items-center gap-2">
                {icon}
                <span className="text-sm font-normal text-[var(--text-primary)]">{title}</span>
            </span>
            <span className="mt-2 text-xs leading-5 text-[var(--text-secondary)]">{description}</span>
        </button>
    );
}

function ScheduleCreateProgress({
    currentStep,
    stepDone,
    onSelect,
}: {
    currentStep: ScheduleCreateStep;
    stepDone: Record<ScheduleCreateStep, boolean>;
    onSelect: (step: ScheduleCreateStep) => void;
}) {
    const currentIndex = Math.max(0, SCHEDULE_CREATE_STEPS.findIndex((step) => step.id === currentStep));

    return (
        <nav className="agent-builder-step-strip schedule-create-step-strip" data-progress={currentIndex + 1}>
            <div className="flex gap-1.5 overflow-x-auto">
                {SCHEDULE_CREATE_STEPS.map((step, index) => {
                    const active = step.id === currentStep;
                    const done = stepDone[step.id] && !active;

                    return (
                        <button
                            key={step.id}
                            type="button"
                            onClick={() => onSelect(step.id)}
                            className={cn(
                                'agent-builder-step-button flex h-8 shrink-0 items-center gap-2 rounded-md px-2 text-left text-xs transition-colors',
                                active ? 'agent-builder-step-button-active' : ''
                            )}
                        >
                            <span className={cn(
                                'agent-builder-step-index flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-medium',
                                done
                                    ? 'agent-builder-step-index-done'
                                    : active
                                        ? 'agent-builder-step-index-active'
                                        : ''
                            )}>
                                {done ? <CheckCircle2 className="h-3.5 w-3.5" /> : index + 1}
                            </span>
                            <span className="min-w-0 truncate font-medium">
                                {step.label}
                            </span>
                        </button>
                    );
                })}
            </div>
            <div className="agent-builder-progress-track" aria-hidden="true">
                <span className="agent-builder-progress-fill" />
            </div>
        </nav>
    );
}

function ScheduleCreatePreview({
    targetKind,
    targetName,
    scheduleType,
    preview,
    filterInstruction,
    timeDescription,
    visibility,
    onVisibilityChange,
}: {
    targetKind: TargetKind;
    targetName: string;
    scheduleType: ScheduleTypeValue;
    preview: string;
    filterInstruction: string;
    timeDescription: string;
    visibility: string;
    onVisibilityChange: (visibility: string) => void;
}) {
    return (
        <aside className="lg:self-start">
            <div className="flex items-start gap-3">
                <ProductIcon kind={targetKind === 'agent' ? 'agents' : 'workflows'} size="lg" />
                <div className="min-w-0">
                    <p className="section-label">Preview</p>
                    <h3 className="mt-1 truncate text-base font-normal text-[var(--text-primary)]">{formatTargetLabel(targetName)}</h3>
                </div>
            </div>

            <p className="resource-soft-inline mt-4 rounded-lg px-3 py-2 text-sm leading-6 text-[var(--text-secondary)]">
                {preview}
            </p>

            <div className="mt-4 space-y-2 text-xs text-[var(--text-secondary)]">
                <PreviewLine done text={targetKind === 'agent' ? 'Agent schedule' : 'Workflow schedule'} />
                <PreviewLine done text={scheduleType.toLowerCase()} />
                {scheduleType === ScheduleType.TIME ? <PreviewLine done text={timeDescription} muted /> : null}
                <PreviewLine done={Boolean(filterInstruction.trim())} text={filterInstruction.trim() ? 'Condition added' : 'No condition'} muted />
            </div>

            <ResourceVisibilitySelect
                value={visibility}
                resourceLabel="schedules"
                resourceName={formatTargetLabel(targetName)}
                onChange={onVisibilityChange}
                className="mt-5"
            />
        </aside>
    );
}

function PreviewLine({ done, text, muted }: { done: boolean; text: ReactNode; muted?: boolean }) {
    return (
        <div className={cn('flex items-center gap-2', muted && !done ? 'text-[var(--text-tertiary)]' : undefined)}>
            <span className={cn(
                'flex h-4 w-4 shrink-0 items-center justify-center rounded-full',
                done
                    ? 'resource-done-dot'
                    : 'readiness-dot-empty'
            )}>
                <CheckCircle2 className="h-2.5 w-2.5" />
            </span>
            <span className="min-w-0 truncate">{text}</span>
        </div>
    );
}
