'use client';

import { use, useMemo, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { ArrowLeft, ArrowRight, CalendarClock, CheckCircle2, Database, Loader2, Pause, Play, Plus, Share2, Sparkles, Webhook, X } from '@/components/ui/icons';
import { toast } from 'sonner';

import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { EmptyState } from '@/components/shared/empty-state';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import {
    ResourceFeedbackBanner,
    showResourceCreatedToast,
    showResourceErrorToast,
} from '@/components/shared/resource-feedback';
import { ResourceShareButton, ResourceVisibilityBadge, ResourceVisibilitySelect, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';
import { ProductIcon } from '@/components/pod/product-icon';
import { ConceptHint } from '@/components/education/concept-hint';
import { SectionPrimer } from '@/components/education/section-primer';
import { ResourceIndexHeader, ResourceIndexShell, ResourceMetricButton } from '@/components/pod/resource-layout';
import { Button } from '@/components/ui/button';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useAgents } from '@/lib/hooks/use-agents';
import { useTables } from '@/lib/hooks/use-datastores';
import { useFlows } from '@/lib/hooks/use-flows';
import { useAccounts, useConnectors, useTriggers } from '@/lib/hooks/use-connectors';
import { useCreateSchedule, useDeleteSchedule, useSchedules, useUpdateSchedule } from '@/lib/hooks/use-schedules';
import { usePod } from '@/lib/hooks/use-pods';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { cn } from '@/lib/utils';
import { formatAgentName } from '@/lib/utils/agents';
import { describeCron, describeScheduleConfig, formatScheduleType, getScheduleConfigDetails, getScheduleTargetKind, getScheduleTargetName } from '@/lib/utils/schedules';
import { ScheduleType, type Account, type CreateScheduleRequest, type Schedule, type Workflow as LemmaWorkflow } from '@/lib/types';

type TargetKind = 'workflow' | 'agent';
type ScheduleFilter = 'all' | 'active' | 'workflow' | 'agent';
type ScheduleTypeValue = `${ScheduleType}`;
type TimeCadence = 'hourly' | 'daily' | 'weekdays' | 'weekly' | 'monthly' | 'custom';

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
type DataOperation = 'INSERT' | 'UPDATE' | 'DELETE';
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
const DATA_OPERATIONS: Array<{ value: DataOperation; label: string; description: string }> = [
    { value: 'INSERT', label: 'New records', description: 'Run when a row is created.' },
    { value: 'UPDATE', label: 'Updates', description: 'Run when a row changes.' },
    { value: 'DELETE', label: 'Deletes', description: 'Run when a row is removed.' },
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

function getScheduleHref(podId: string, schedule: Schedule): string {
    if (schedule.workflow_name) return `/pod/${podId}/flows/${encodeURIComponent(schedule.workflow_name)}`;
    if (schedule.agent_name) return `/pod/${podId}/agents/${encodeURIComponent(schedule.agent_name)}`;
    return `/pod/${podId}/schedules`;
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
    return value ? formatAgentName(value) : 'Untitled';
}

function getScheduleEmptyCopy(filter: ScheduleFilter): { title: string; description: string } {
    if (filter === 'active') {
        return {
            title: 'No active schedules',
            description: 'Paused schedules stay here, but nothing is currently listening or waking up work.',
        };
    }
    if (filter === 'workflow') {
        return {
            title: 'No workflow schedules',
            description: 'Create one when a workflow should run on a rhythm, event, or data change.',
        };
    }
    if (filter === 'agent') {
        return {
            title: 'No agent schedules',
            description: 'Create one when an agent should wake up without someone starting it manually.',
        };
    }
    return {
        title: 'No schedules yet',
        description: 'Create a schedule when a workflow or agent should wake up on a rhythm, event, or data change.',
    };
}

export default function PodSchedulesPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const searchParams = useSearchParams();
    const podAccess = usePodAccess(podId);
    const canCreateSchedule = podAccess.can('schedule.create');
    const canUpdateSchedule = podAccess.can('schedule.update');
    const canDeleteSchedule = podAccess.can('schedule.delete');
    const requestedWorkflow = searchParams.get('workflow') || '';
    const requestedAgent = searchParams.get('agent') || '';

    const { data: schedulesData, isLoading: loadingSchedules } = useSchedules(podId, { limit: 100 });
    const { data: pod } = usePod(podId);
    const { data: flowsData, isLoading: loadingFlows } = useFlows(podId);
    const { data: agentsData, isLoading: loadingAgents } = useAgents(podId);
    const { data: tablesData } = useTables(podId);
    const { data: connectors = [] } = useConnectors({ limit: 100 });
    const { data: accounts = [] } = useAccounts({ organizationId: pod?.organization_id, limit: 100 });

    const schedules = useMemo(() => schedulesData?.items || [], [schedulesData?.items]);
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
    const [isCreateOpen, setIsCreateOpen] = useState(false);
    const [createStep, setCreateStep] = useState<ScheduleCreateStep>('worker');
    const [scheduleFilter, setScheduleFilter] = useState<ScheduleFilter>('all');
    const [createNotice, setCreateNotice] = useState<{ title: string; description: string } | null>(null);
    const [schedulePendingDelete, setSchedulePendingDelete] = useState<Schedule | null>(null);

    const { data: triggers = [] } = useTriggers({
        organizationId: pod?.organization_id,
        connectorId,
        limit: 100,
        enabled: scheduleType === ScheduleType.WEBHOOK && !!connectorId,
    });

    const createSchedule = useCreateSchedule(podId);
    const updateSchedule = useUpdateSchedule(podId);
    const deleteSchedule = useDeleteSchedule(podId);

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

    const filteredSchedules = useMemo(() => {
        return schedules.filter((schedule) => {
            const matchesFilter =
                scheduleFilter === 'all'
                || (scheduleFilter === 'active' && schedule.is_active !== false)
                || (scheduleFilter === 'workflow' && getScheduleTargetKind(schedule) === 'workflow')
                || (scheduleFilter === 'agent' && getScheduleTargetKind(schedule) === 'agent');
            return matchesFilter;
        });
    }, [scheduleFilter, schedules]);

    const activeCount = schedules.filter((schedule) => schedule.is_active !== false).length;
    const workflowScheduleCount = schedules.filter((schedule) => getScheduleTargetKind(schedule) === 'workflow').length;
    const agentScheduleCount = schedules.filter((schedule) => getScheduleTargetKind(schedule) === 'agent').length;
    const workflowByName = useMemo(() => {
        return new Map(workflows.map((workflow) => [workflow.name, workflow]));
    }, [workflows]);
    const isLoading = loadingSchedules || loadingFlows || loadingAgents;
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
    const scheduleEmptyCopy = getScheduleEmptyCopy(scheduleFilter);

    const goToNextCreateStep = () => {
        if (!canCreateSchedule) return;
        if (!canContinueCreate) return;
        const nextStep = SCHEDULE_CREATE_STEPS[Math.min(SCHEDULE_CREATE_STEPS.length - 1, currentCreateStepIndex + 1)];
        setCreateStep(nextStep.id);
    };

    const goToPreviousCreateStep = () => {
        if (!canCreateSchedule) return;
        const previousStep = SCHEDULE_CREATE_STEPS[Math.max(0, currentCreateStepIndex - 1)];
        setCreateStep(previousStep.id);
    };

    const openCreate = () => {
        if (!canCreateSchedule) return;
        setCreateNotice(null);
        setIsCreateOpen(true);
        setCreateStep('worker');
    };

    const closeCreate = () => {
        setIsCreateOpen(false);
        setCreateStep('worker');
    };

    const handleCreate = async () => {
        if (!canCreateSchedule) return;
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
            setCreateNotice({
                title: 'Schedule created',
                description: `Automation is listening. ${schedulePreview}`,
            });
            closeCreate();
        } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to create schedule';
            showResourceErrorToast(error, message);
        }
    };

    const handleToggle = async (schedule: Schedule) => {
        if (!resourceAllows(schedule, 'schedule.update', canUpdateSchedule)) return;
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
        if (!schedulePendingDelete) return;
        if (!resourceAllows(schedulePendingDelete, 'schedule.delete', canDeleteSchedule)) return;
        try {
            await deleteSchedule.mutateAsync(schedulePendingDelete.id);
            toast.success('Schedule deleted');
            setSchedulePendingDelete(null);
        } catch {
            toast.error('Failed to delete schedule');
        }
    };

    return (
        <ResourceIndexShell>
            <ResourceIndexHeader
                title="Schedules"
                productIconKind="schedules"
                meta={<ConceptHint concept="schedule" />}
                actions={canCreateSchedule ? (
                    <Button asChild size="sm" className="gap-2">
                        <Link href={`/pod/${podId}/schedules/new`}>
                            <Plus className="h-4 w-4" />
                            New schedule
                        </Link>
                    </Button>
                ) : null}
            />

            <SectionPrimer concept="schedule" className="mb-4" />

            {createNotice ? (
                <ResourceFeedbackBanner
                    tone="success"
                    title={createNotice.title}
                    description={createNotice.description}
                    celebrate
                    onDismiss={() => setCreateNotice(null)}
                    actions={canCreateSchedule ? [
                        { label: 'New schedule', onClick: openCreate, variant: 'primary' },
                    ] : []}
                    className="mb-5"
                />
            ) : null}

            {isCreateOpen && canCreateSchedule ? (
                <section className="mb-5">
                    <div className="resource-divider-bottom mb-4 flex items-start justify-between gap-3 border-b pb-4">
                        <div className="min-w-0">
                            <p className="section-label">New schedule</p>
                            <h2 className="mt-1 text-lg font-normal text-[var(--text-primary)]">Teach this pod a habit</h2>
                            <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">Choose what should run, when it should wake up, and any condition it should respect.</p>
                        </div>
                        <button
                            type="button"
                            onClick={closeCreate}
                            className="lemma-quiet-icon-button custom-focus-ring text-[var(--text-tertiary)]"
                            aria-label="Cancel schedule creation"
                            title="Cancel"
                        >
                            <X className="h-4 w-4" />
                        </button>
                    </div>

                    <ScheduleCreateProgress currentStep={createStep} stepDone={stepDone} onSelect={setCreateStep} />

                    <div className="mt-5 grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
                        <div className="min-w-0">
                            <div className="mb-4">
                                <p className="section-label">{SCHEDULE_CREATE_STEPS[currentCreateStepIndex].eyebrow}</p>
                                <h3 className="mt-1 text-lg font-normal text-[var(--text-primary)]">{SCHEDULE_CREATE_STEPS[currentCreateStepIndex].label}</h3>
                                <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">{SCHEDULE_CREATE_STEPS[currentCreateStepIndex].description}</p>
                            </div>

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
                                                        {formatAgentName(item.name)}
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
                                <div className="space-y-4">
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
                                </div>
                            ) : null}

                            <div className="resource-divider-top mt-5 flex items-center justify-between gap-3 border-t pt-4">
                                <Button type="button" variant="outline" size="sm" className="gap-2" onClick={goToPreviousCreateStep} disabled={createStep === 'worker' || createSchedule.isPending}>
                                    <ArrowLeft className="h-3.5 w-3.5" />
                                    Back
                                </Button>
                                {createStep === 'review' ? (
                                    <Button type="button" size="sm" className="gap-2" onClick={() => void handleCreate()} disabled={createSchedule.isPending || !selectedTargetName}>
                                        {createSchedule.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                                        {createSchedule.isPending ? 'Creating...' : 'Create schedule'}
                                    </Button>
                                ) : (
                                    <Button type="button" size="sm" className="gap-2" onClick={goToNextCreateStep} disabled={!canContinueCreate || createSchedule.isPending}>
                                        Next
                                        <ArrowRight className="h-3.5 w-3.5" />
                                    </Button>
                                )}
                            </div>
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
            ) : null}

            <div className="lemma-index-tabs lemma-index-tabs-left flex-wrap">
                <ResourceMetricButton active={scheduleFilter === 'all'} label="Schedules" count={schedules.length} onClick={() => setScheduleFilter('all')} />
                <ResourceMetricButton active={scheduleFilter === 'active'} label="Active" count={activeCount} onClick={() => setScheduleFilter('active')} />
                <ResourceMetricButton active={scheduleFilter === 'workflow'} label="Workflows" count={workflowScheduleCount} onClick={() => setScheduleFilter('workflow')} />
                <ResourceMetricButton active={scheduleFilter === 'agent'} label="Agents" count={agentScheduleCount} onClick={() => setScheduleFilter('agent')} />
            </div>

            <section className="resource-index-grid resource-index-grid-md-2 resource-index-grid-xl-3 min-w-0 md:grid-cols-2 xl:grid-cols-3">
                    {isLoading ? (
                        <div className="flex items-center justify-center py-16">
                            <Loader2 className="h-5 w-5 animate-spin text-[var(--text-tertiary)]" />
                        </div>
                    ) : filteredSchedules.length === 0 ? (
                        <EmptyState
                            variant="panel"
                            icon={<CalendarClock className="h-5 w-5" />}
                            title={scheduleEmptyCopy.title}
                            description={scheduleEmptyCopy.description}
                            action={scheduleFilter === 'all' && canCreateSchedule ? (
                                <Button asChild size="sm" className="gap-2">
                                    <Link href={`/pod/${podId}/schedules/new`}>
                                        <Plus className="h-4 w-4" />
                                        New schedule
                                    </Link>
                                </Button>
                            ) : null}
                            className="md:col-span-2 xl:col-span-3"
                        />
                    ) : (
                        filteredSchedules.map((schedule) => (
                            <ScheduleRow
                                key={schedule.id}
                                podId={podId}
                                schedule={schedule}
                                workflow={schedule.workflow_name ? workflowByName.get(schedule.workflow_name) : undefined}
                                isMutating={updateSchedule.isPending || deleteSchedule.isPending}
                                canUpdate={resourceAllows(schedule, 'schedule.update', canUpdateSchedule)}
                                canDelete={resourceAllows(schedule, 'schedule.delete', canDeleteSchedule)}
                                onToggle={() => void handleToggle(schedule)}
                                onDelete={() => setSchedulePendingDelete(schedule)}
                                onShareVisibilityChange={async (visibility) => {
                                    await updateSchedule.mutateAsync({
                                        scheduleId: schedule.id,
                                        data: { visibility },
                                    });
                                }}
                            />
                        ))
                    )}
                </section>
            <DestructiveConfirmationDialog
                open={Boolean(schedulePendingDelete)}
                onOpenChange={(open) => {
                    if (!open) setSchedulePendingDelete(null);
                }}
                title="Delete schedule"
                description={`Delete schedule for ${schedulePendingDelete ? formatAgentName(getScheduleTargetName(schedulePendingDelete)) : 'this target'}?`}
                resourceName={schedulePendingDelete ? formatAgentName(getScheduleTargetName(schedulePendingDelete)) : 'schedule'}
                confirmationText=""
                consequences={[
                    'This stops future automatic runs for the selected workflow or agent.',
                    'Existing run history is not deleted.',
                ]}
                confirmLabel="Delete schedule"
                pendingLabel="Deleting schedule..."
                isPending={deleteSchedule.isPending}
                onConfirm={() => void handleDelete()}
            />
        </ResourceIndexShell>
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
                            <span className="min-w-0">
                                <span className={cn(
                                    'block truncate font-medium',
                                    active ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)]'
                                )}>
                                    {step.label}
                                </span>
                                <span className="block truncate text-xs text-[var(--text-tertiary)]">{step.eyebrow}</span>
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
                    <h3 className="mt-1 truncate text-base font-semibold text-[var(--text-primary)]">{formatTargetLabel(targetName)}</h3>
                </div>
            </div>

            <p className="resource-soft-inline mt-4 rounded-lg px-3 py-2 text-sm leading-6 text-[var(--text-secondary)]">
                {preview}
            </p>

            <div className="mt-4 space-y-2 text-xs text-[var(--text-secondary)]">
                <PreviewLine done text={targetKind === 'agent' ? 'Agent schedule' : 'Workflow schedule'} />
                <PreviewLine done text={formatScheduleType(scheduleType)} />
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

function ScheduleRow({
    podId,
    schedule,
    workflow,
    isMutating,
    canUpdate,
    canDelete,
    onToggle,
    onDelete,
    onShareVisibilityChange,
}: {
    podId: string;
    schedule: Schedule;
    workflow?: LemmaWorkflow;
    isMutating: boolean;
    canUpdate: boolean;
    canDelete: boolean;
    onToggle: () => void;
    onDelete: () => void;
    onShareVisibilityChange: (visibility: ResourceVisibilityValue) => Promise<void>;
}) {
    const kind = getScheduleTargetKind(schedule);
    const targetName = formatAgentName(getScheduleTargetName(schedule));
    const active = schedule.is_active !== false;
    const workflowEventConfig = schedule.schedule_type === ScheduleType.WEBHOOK ? getEventConfig(workflow) : null;
    const configDetails = [
        ...(workflowEventConfig?.connector_id ? [{ label: 'App', value: workflowEventConfig.connector_id }] : []),
        ...(workflowEventConfig?.connector_trigger_id ? [{ label: 'Trigger', value: workflowEventConfig.connector_trigger_id }] : []),
        ...getScheduleConfigDetails(schedule),
    ];
    const scheduleDescription = workflowEventConfig?.connector_trigger_id
        ? `On ${workflowEventConfig.connector_trigger_id}${workflowEventConfig.connector_id ? ` from ${workflowEventConfig.connector_id}` : ''}`
        : describeScheduleConfig(schedule);
    // Trigger type drives the glyph; target kind (workflow/agent) closes the flow line.
    const triggerKind = schedule.schedule_type === ScheduleType.DATASTORE
        ? 'data'
        : schedule.schedule_type === ScheduleType.WEBHOOK
            ? 'connectors'
            : 'schedules';
    const targetIconKind = kind === 'agent' ? 'agents' : 'workflows';
    const tableDetail = configDetails.find((detail) => detail.label === 'Table');
    const opsDetail = configDetails.find((detail) => detail.label === 'Ops');
    const hasMenuActions = canUpdate || canDelete;
    const scheduleShareUrl = typeof window === 'undefined'
        ? undefined
        : `${window.location.origin}${getScheduleHref(podId, schedule)}`;

    return (
        <div className="resource-index-card group p-4">
            <div className="flex items-start gap-3">
                <Link href={getScheduleHref(podId, schedule)} className="flex min-w-0 flex-1 items-center gap-3">
                    <ProductIcon kind={triggerKind} size="lg" />
                    <p className="truncate font-display text-base font-semibold text-[var(--text-primary)]">{targetName}</p>
                </Link>
                <div className="flex shrink-0 flex-col items-end gap-2">
                    <span className={cn(
                        'inline-flex items-center gap-1.5 text-xs font-medium',
                        active ? 'text-[var(--state-success)]' : 'text-[var(--text-tertiary)]'
                    )}>
                        <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
                        {active ? 'Active' : 'Paused'}
                    </span>
                    {hasMenuActions ? (
                        <ResourceActionsMenu
                            ariaLabel={`Open actions for schedule ${targetName}`}
                            align="end"
                            triggerClassName="h-7 w-7 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                        >
                        {canUpdate ? (
                            <ResourceShareButton
                                value={schedule.visibility}
                                podId={podId}
                                resourceType="schedule"
                                resourceId={schedule.id}
                                resourceLabel="schedules"
                                resourceName={targetName}
                                shareUrl={scheduleShareUrl}
                                onChange={onShareVisibilityChange}
                                className="contents"
                                trigger={({ openShare, disabled }) => (
                                    <DropdownMenuItem
                                        disabled={disabled || isMutating}
                                        onSelect={(event) => {
                                            event.preventDefault();
                                            openShare();
                                        }}
                                    >
                                        <Share2 className="mr-2 h-4 w-4" />
                                        Share
                                    </DropdownMenuItem>
                                )}
                            />
                        ) : null}
                        {canUpdate ? (
                            <DropdownMenuItem
                                disabled={isMutating}
                                onSelect={(event) => {
                                    event.preventDefault();
                                    onToggle();
                                }}
                            >
                                {active ? <Pause className="mr-2 h-4 w-4" /> : <Play className="mr-2 h-4 w-4" />}
                                {active ? 'Pause schedule' : 'Resume schedule'}
                            </DropdownMenuItem>
                        ) : null}
                        {canDelete ? (
                            <DestructiveResourceActionItem disabled={isMutating} onSelect={onDelete}>
                                Delete schedule
                            </DestructiveResourceActionItem>
                        ) : null}
                    </ResourceActionsMenu>
                    ) : null}
                </div>
            </div>

            <Link href={getScheduleHref(podId, schedule)} className="mt-3 block">
                <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm leading-5 text-[var(--text-secondary)]">
                    {tableDetail ? (
                        <>
                            <code className="rounded border border-[var(--border-subtle)] bg-[var(--surface-2)] px-1.5 py-0.5 font-mono text-xs text-[var(--text-primary)]">{tableDetail.value}</code>
                            {opsDetail ? (
                                <span className="text-xs text-[var(--text-tertiary)]">{opsDetail.value.toLowerCase()}</span>
                            ) : null}
                        </>
                    ) : (
                        <span className="min-w-0 truncate">{scheduleDescription}</span>
                    )}
                    <ArrowRight className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" aria-hidden />
                    <span className="inline-flex items-center gap-1 text-[var(--text-secondary)]">
                        <ProductIcon kind={targetIconKind} size="xs" />
                        {kind === 'agent' ? 'agent' : 'workflow'}
                    </span>
                    <ResourceVisibilityBadge visibility={schedule.visibility} resourceLabel="schedules" hideWhenDefault />
                </div>
                {schedule.filter_instruction ? (
                    <p className="mt-2 truncate text-xs text-[var(--text-tertiary)]">Filter: {schedule.filter_instruction}</p>
                ) : null}
            </Link>
        </div>
    );
}
