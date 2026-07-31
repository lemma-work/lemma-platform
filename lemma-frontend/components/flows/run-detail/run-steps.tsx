import { useEffect, useMemo, useRef, useState } from 'react';
import { useAssistantController, useConversationMessages } from 'lemma-sdk/react';
import {
    Bot,
    ChevronDown,
    ChevronRight,
    Clock,
    Loader2,
    MessageCircle,
    XCircle,
} from '@/components/ui/icons';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { getPreviewFields, truncatePreview } from '@/lib/utils/payload-preview';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { AssistantExperienceView } from '@/components/lemma/assistant/assistant-experience';
import type { AssistantControllerView } from '@/components/lemma/assistant/assistant-types';
import { NodeType, WorkflowNode } from '@/lib/types';
import {
    COMPLETE_STATUSES,
    FAILURE_STATUSES,
    extractConversationContentText,
    formatDuration,
    formatPreciseDuration,
    formatStructuredOutput,
    formatTimestamp,
    getAgentConversationId,
    getDisplayNodeLabel,
    getFinalStructuredOutput,
    getNodeAgentName,
    getNodeIconElement,
    getNodeTypeLabel,
    getProcedureStatusForVariant,
    getProcedureStepStatusLabel,
    getStepNarrative,
    getStepSummaryText,
    getStatusVariant,
    getVisibleStepData,
    hasVisibleData,
    isActiveStepStatus,
    isReachedStepState,
    isRecord,
    parseApiDate,
    toRunStatus,
    type ProcedureStepState,
    type RunCardRun,
    type StatusVariant,
} from '../run-format';
import { StepDot } from './step-dots';
import { StepDataPreview } from './step-data-preview';
import { RunInputForm } from './run-input-form';
import {
    RunCompletionCard,
    RunCompletionChamber,
    getLastVisibleOutputBeforeStep,
    getRunCompletionTiming,
} from './run-completion';

export function RunPlaybackStep({
    podId,
    node,
    step,
    previousStep,
    run,
    index,
    totalSteps,
    state,
    runStatus,
    nextNodeLabel,
    onRunRefresh,
    onSubmitInput,
    chamber = false,
}: {
    podId: string;
    node: WorkflowNode;
    step: Record<string, unknown> | null;
    previousStep: Record<string, unknown> | null;
    run: RunCardRun;
    index: number;
    totalSteps: number;
    state: ProcedureStepState;
    runStatus: string;
    nextNodeLabel: string | null;
    onRunRefresh?: () => Promise<void> | void;
    onSubmitInput: (nodeId: string, data: Record<string, unknown>) => Promise<void>;
    chamber?: boolean;
}) {
    const stepStatus = toRunStatus(step?.status || runStatus);
    const runActiveWait = (run as {
        active_wait?: {
            wait_type?: string;
            node_id?: string;
            external_ref?: string | null;
            payload?: { input_schema?: Record<string, unknown> | null } | null;
        } | null;
    })?.active_wait ?? null;
    // The form schema is resolved server-side and rides on the active wait;
    // prefer it over the node's static (templated) config.
    const formWaitSchema =
        runActiveWait?.wait_type === 'HUMAN' && runActiveWait.node_id === node.id
            ? runActiveWait.payload?.input_schema ?? null
            : null;
    // Suspend metadata lives on the run's active wait, not in step output.
    const agentConversationId = (
        runActiveWait?.wait_type === 'AGENT' && runActiveWait.node_id === node.id
            ? runActiveWait.external_ref ?? null
            : null
    ) ?? getAgentConversationId(step?.output_data);
    const hasReachedStep = isReachedStepState(state);
    const outputData = getVisibleStepData(step?.output_data);
    const inputData = getVisibleStepData(step?.input_data) || getVisibleStepData(previousStep?.output_data);
    const showInput = hasReachedStep && node.type !== NodeType.FORM && hasVisibleData(inputData);
    const showOutput = hasReachedStep && hasVisibleData(outputData) && node.type !== NodeType.AGENT;
    const timing = getStepTiming(step);
    const timingText = getStepTimingText(timing, state);
    const isWaitingForInput = node.type === NodeType.FORM && state === 'waiting';
    const isAgentStage = node.type === NodeType.AGENT;
    const isCompletedEndStep = node.type === NodeType.END && state === 'completed';
    const completionTiming = getRunCompletionTiming(run, step);
    const completionOutput = hasVisibleData(outputData)
        ? outputData
        : getVisibleStepData(previousStep?.output_data) || getLastVisibleOutputBeforeStep(run, step);
    const previousStepLabel = index > 0 ? `step ${index}` : 'the previous step';

    if (chamber) {
        if (!hasReachedStep) {
            return (
                <PendingStepChamber
                    node={node}
                    index={index}
                    state={state}
                    previousStepLabel={previousStepLabel}
                    runStatus={runStatus}
                />
            );
        }

        if (isCompletedEndStep) {
            return (
                <RunCompletionChamber
                    timing={completionTiming}
                    eventCount={run.step_history?.length || totalSteps}
                    output={completionOutput}
                />
            );
        }

        if (isAgentStage) {
            return (
                <AgentStepChamber
                    podId={podId}
                    node={node}
                    stepStatus={stepStatus}
                    conversationId={agentConversationId}
                    inputData={inputData}
                    outputData={outputData}
                    state={state}
                    onAgentSettled={onRunRefresh}
                />
            );
        }

        return (
            <section className="h-full min-h-0 overflow-y-auto px-6 py-5">
                <div className="space-y-6">
                    {showInput ? <StepDataPreview label="Input" data={inputData} variant="flat" /> : null}
                    {isWaitingForInput ? (
                        <RunInputForm
                            nodeId={node.id}
                            nodes={[node]}
                            schema={formWaitSchema}
                            nextNodeLabel={nextNodeLabel}
                            onSubmitInput={onSubmitInput}
                            variant="flat"
                        />
                    ) : null}
                    {node.type === NodeType.FUNCTION && state === 'running' ? (
                        <div className="flex min-h-40 items-center gap-3 text-sm text-[var(--text-secondary)]">
                            <Loader2 className="h-4 w-4 animate-spin text-[var(--text-primary)]" />
                            Executing function. Output will appear here when it returns.
                        </div>
                    ) : null}
                    {showOutput ? <StepDataPreview label={node.type === NodeType.FORM ? 'Submitted input' : 'Output'} data={outputData} variant="flat" /> : null}
                </div>
            </section>
        );
    }

    return (
        <section className={cn('relative grid gap-4', chamber ? 'h-full grid-cols-1' : 'grid-cols-[2.5rem_minmax(0,1fr)]')}>
            {!chamber ? <StepDot state={state} type={node.type} /> : null}
            <div
                className={cn(
                    'surface-panel px-4 py-4',
                    chamber ? 'min-h-full border-0 shadow-none' : '',
                    state === 'running' && 'state-surface-running',
                    state === 'waiting' && 'state-surface-warning',
                    state === 'completed' && 'state-surface-success',
                    state === 'failed' && 'state-surface-error'
                )}
            >
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                    <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--surface-2)] text-xs font-semibold text-[var(--text-secondary)]">
                                {index + 1}
                            </span>
                            <h3 className="truncate text-base font-semibold text-[var(--text-primary)]">{getDisplayNodeLabel(node)}</h3>
                            <span className="chip chip-pill chip-sm chip-muted type-micro-label">
                                {getNodeTypeLabel(node.type)}
                            </span>
                            <Badge variant={getStatusVariant(state === 'running' ? stepStatus : getProcedureStatusForVariant(state))} className="flow-execution-badge">
                                {getProcedureStepStatusLabel(state)}
                            </Badge>
                        </div>
                        <p className="mt-2 text-sm text-[var(--text-secondary)]">
                            {getStepNarrative({ node, state, nextNodeLabel, index, totalSteps })}
                        </p>
                    </div>
                    <div className="flex shrink-0 flex-wrap gap-2 text-xs text-[var(--text-secondary)] md:justify-end">
                        {timingText.map((item) => (
                            <span key={item} className="chip chip-pill chip-sm chip-muted">{item}</span>
                        ))}
                    </div>
                </div>

                {(showInput || isWaitingForInput || (isAgentStage && hasReachedStep) || showOutput || state === 'completed') ? (
                    <div className="mt-4 space-y-3">
                        {showInput ? <StepDataPreview label="Input" data={inputData} /> : null}
                        {isWaitingForInput ? (
                            <RunInputForm
                                nodeId={node.id}
                                nodes={[node]}
                                schema={formWaitSchema}
                                nextNodeLabel={nextNodeLabel}
                                onSubmitInput={onSubmitInput}
                            />
                        ) : null}
                        {isAgentStage && agentConversationId ? (
                            <EmbeddedAgentConversationProgress
                                podId={podId}
                                conversationId={agentConversationId}
                                stepStatus={stepStatus}
                                onAgentSettled={onRunRefresh}
                            />
                        ) : isAgentStage && hasVisibleData(outputData) ? (
                            <AgentResultCard data={outputData} />
                        ) : isAgentStage && state === 'running' ? (
                            <div className="signal-surface-intelligence rounded-lg px-3 py-3">
                                <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                                    <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--intelligence)]" />
                                    Waiting for the first agent message.
                                </div>
                            </div>
                        ) : null}
                        {showOutput ? <StepDataPreview label={node.type === NodeType.FORM ? 'Submitted input' : 'Output'} data={outputData} /> : null}
                        {isCompletedEndStep ? <RunCompletionCard timing={completionTiming} totalSteps={totalSteps} output={completionOutput} /> : null}
                    </div>
                ) : null}
            </div>
        </section>
    );
}

function PendingStepChamber({
    node,
    index,
    state,
    previousStepLabel,
    runStatus,
}: {
    node: WorkflowNode;
    index: number;
    state: ProcedureStepState;
    previousStepLabel: string;
    runStatus: string;
}) {
    const runFinished = COMPLETE_STATUSES.has(runStatus) || FAILURE_STATUSES.has(runStatus);

    return (
        <section className="flex h-full min-h-[320px] items-center justify-center">
            <div className="max-w-md text-center">
                <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-[var(--surface-2)] text-[var(--text-tertiary)]">
                    {state === 'next' ? <Clock className="h-7 w-7" /> : getNodeIconElement(node.type)}
                </div>
                <div className="mt-5 chip chip-pill chip-md chip-muted type-micro-label">
                    Step {index + 1} not reached
                </div>
                <h3 className="mt-4 text-xl font-semibold text-[var(--text-primary)]">{getDisplayNodeLabel(node)} is waiting</h3>
                <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">
                    {runFinished
                        ? 'This step was not reached by the path this run took.'
                        : `This step has not started in this run yet. It will open here after ${previousStepLabel} finishes.`}
                </p>
            </div>
        </section>
    );
}

function AgentStepChamber({
    podId,
    node,
    stepStatus,
    conversationId,
    inputData,
    outputData,
    state,
    onAgentSettled,
}: {
    podId: string;
    node: WorkflowNode;
    stepStatus: string;
    conversationId: string | null;
    inputData: unknown;
    outputData: unknown;
    state: ProcedureStepState;
    onAgentSettled?: () => Promise<void> | void;
}) {
    const agentName = getNodeAgentName(node);
    const client = useMemo(() => getLemmaClient(podId), [podId]);
    const settledConversationRef = useRef<string | null>(null);
    const controller = useAssistantController({
        client,
        podId,
        agentName: agentName || undefined,
        enabled: Boolean(agentName || conversationId),
    });
    const openedConversationId = controller.openedConversationId;
    const openConversation = controller.openConversation;

    useEffect(() => {
        if (!conversationId || openedConversationId === conversationId) return;
        openConversation(conversationId);
    }, [conversationId, openConversation, openedConversationId]);

    useEffect(() => {
        if (!conversationId) {
            settledConversationRef.current = null;
            return;
        }

        if (controller.isOpenedConversationRunning || controller.isLoading) {
            if (controller.isOpenedConversationRunning) settledConversationRef.current = null;
            return;
        }

        const hasSettledSignal = controller.messages.length > 0 || hasVisibleData(outputData);
        if (!hasSettledSignal) return;
        if (settledConversationRef.current === conversationId) return;

        settledConversationRef.current = conversationId;
        void onAgentSettled?.();
    }, [
        conversationId,
        controller.isOpenedConversationRunning,
        controller.isLoading,
        controller.messages.length,
        onAgentSettled,
        outputData,
    ]);

    const controllerView = useMemo<AssistantControllerView>(
        () => controller as unknown as AssistantControllerView,
        [controller]
    );
    const hasConversationSurface = Boolean(agentName || conversationId);
    const isBusy = controller.isOpenedConversationRunning || controller.isLoading || isActiveStepStatus(stepStatus) || state === 'running';
    const fallbackText = getStepSummaryText(outputData) || (hasVisibleData(outputData) ? formatStructuredOutput(outputData) : '');
    const showInput = hasVisibleData(inputData);
    const showOutput = hasVisibleData(outputData);

    return (
        <section className="flex h-full min-h-0 flex-col bg-[var(--card-bg)]">
            {(showInput || showOutput) ? (
                <div className="shrink-0 border-b border-[color:var(--row-border)] bg-[var(--card-bg)] px-4 py-3">
                    <div className={cn('grid gap-3', showInput && showOutput ? 'xl:grid-cols-2' : 'grid-cols-1')}>
                        {showInput ? <StepDataPreview label="Input" data={inputData} variant="flat" /> : null}
                        {showOutput ? <StepDataPreview label={state === 'failed' ? 'Error output' : 'Output'} data={outputData} variant="flat" /> : null}
                    </div>
                </div>
            ) : null}
            <div className="min-h-0 flex-1 overflow-hidden">
                {hasConversationSurface ? (
                    <AssistantExperienceView
                        controller={controllerView}
                        title={agentName || 'Agent'}
                        subtitle={null}
                        appearance="borderless"
                        density="compact"
                        chromeStyle="flat"
                        radius="none"
                        statusPlacement="inline"
                        showHeader={false}
                        showModelPicker={false}
                        showNewConversationButton={false}
                        showFinalOutput={false}
                        placeholder={isBusy ? 'Message the agent while it works...' : 'Follow up with the agent...'}
                        className="h-full min-h-0 rounded-none border-0 bg-[var(--card-bg)] shadow-none"
                        contentWidthClassName="max-w-none gap-4"
                        composerWidthClassName="max-w-none"
                        emptyState={(
                            <div className="flex h-full min-h-[220px] items-center justify-center text-sm text-[var(--text-secondary)]">
                                {isBusy ? 'Waiting for the first agent message.' : 'No agent messages yet.'}
                            </div>
                        )}
                    />
                ) : fallbackText ? (
                    <div className="h-full overflow-y-auto px-6 py-5">
                        <div className="max-w-4xl whitespace-pre-wrap break-words text-sm leading-7 text-[var(--text-primary)]">
                        {truncatePreview(humanizeAgentMessage(fallbackText), 1400)}
                        </div>
                    </div>
                ) : (
                    <div className="flex h-full min-h-[220px] items-center gap-3 text-sm text-[var(--text-secondary)]">
                        {state === 'failed' ? <XCircle className="h-4 w-4 text-[var(--state-error)]" /> : <Loader2 className="h-4 w-4 animate-spin text-[var(--text-primary)]" />}
                        {state === 'failed' ? 'No agent transcript was recorded.' : 'Waiting for the agent to start.'}
                    </div>
                )}
            </div>
        </section>
    );
}

function getStepTiming(step: Record<string, unknown> | null | undefined): {
    startedAt: Date | null;
    completedAt: Date | null;
    duration: string | null;
} {
    if (!step) return { startedAt: null, completedAt: null, duration: null };
    return {
        startedAt: parseApiDate(step.started_at),
        completedAt: parseApiDate(step.completed_at),
        duration: formatPreciseDuration(step.started_at, step.completed_at),
    };
}

function getStepTimingText(
    timing: { startedAt: Date | null; completedAt: Date | null; duration: string | null },
    state: ProcedureStepState
): string[] {
    const parts: string[] = [];

    if (timing.startedAt) parts.push(`Started ${formatTimestamp(timing.startedAt)}`);
    if (timing.completedAt) parts.push(`Finished ${formatTimestamp(timing.completedAt)}`);
    if (timing.duration) parts.push(timing.duration);

    if (!timing.duration && state === 'running' && timing.startedAt) {
        const liveDuration = formatDuration(timing.startedAt, new Date());
        if (liveDuration) parts.push(`${liveDuration} so far`);
    }

    if (parts.length === 0) {
        parts.push(getProcedureStepStatusLabel(state));
    }

    return parts;
}

function getConversationMessageLabel(message: {
    role: string;
    kind?: string | null;
    metadata?: Record<string, unknown> | null;
    tool_name?: string | null;
}): string {
    const metadata = message.metadata;
    if (isRecord(metadata) && metadata.is_final_answer === true) return 'Result';

    const role = message.role.toLowerCase();
    if (role === 'user') return 'Input';
    if (role === 'tool') return message.tool_name || 'Tool';

    if (role === 'assistant') {
        if (message.kind === 'THINKING' || message.kind === 'thinking') return 'Thought';
        if (message.kind === 'TOOL_CALL' || message.kind === 'tool_call') return 'Tool call';
        return 'Agent';
    }

    return role || 'Message';
}

function getConversationMessagePreviewText(message: {
    role: string;
    kind?: string | null;
    text?: string | null;
    metadata?: Record<string, unknown> | null;
    tool_name?: string | null;
}): string {
    const metadata = message.metadata;
    if (isRecord(metadata) && metadata.is_final_answer === true && hasVisibleData(metadata.structured_output)) {
        return formatStructuredOutput(metadata.structured_output);
    }

    if (message.kind === 'TOOL_CALL' || message.kind === 'tool_call') {
        return message.tool_name ? `${message.tool_name} called` : 'Tool called';
    }

    if (message.kind === 'TOOL_RETURN' || message.kind === 'tool_return') {
        return message.tool_name ? `${message.tool_name} completed` : 'Tool completed';
    }

    const text = extractConversationContentText(message.text);
    if (text) return text;

    if (message.role.toLowerCase() === 'tool') {
        return message.tool_name ? `${message.tool_name} completed` : 'Tool completed';
    }

    return '';
}

function humanizeAgentMessage(text: string): string {
    const workflowInputPrefix = 'Workflow input JSON:';
    if (text.startsWith(workflowInputPrefix)) {
        const payloadText = text.slice(workflowInputPrefix.length).trim();
        try {
            const payload = JSON.parse(payloadText);
            if (isRecord(payload)) {
                const preview = getPreviewFields(payload)
                    .slice(0, 3)
                    .map((field) => `${field.label}: ${field.value}`)
                    .join(', ');
                if (preview) return `Input received: ${preview}`;
            }
        } catch {
            return 'Input received.';
        }
    }

    return text;
}

function EmbeddedAgentConversationProgress({
    podId,
    conversationId,
    stepStatus,
    onAgentSettled,
}: {
    podId: string;
    conversationId: string;
    stepStatus: string;
    onAgentSettled?: () => Promise<void> | void;
}) {
    const [isExpanded, setIsExpanded] = useState(false);
    const settledConversationRef = useRef<string | null>(null);
    const client = useMemo(() => getLemmaClient(podId), [podId]);
    const conversation = useConversationMessages({
        client,
        podId,
        conversationId,
        enabled: Boolean(conversationId),
        autoLoad: true,
        autoResume: isActiveStepStatus(stepStatus),
        limit: 50,
        syncOnTurnEnd: true,
    });

    const structuredFinalOutput = useMemo(
        () => getFinalStructuredOutput(conversation.messages),
        [conversation.messages]
    );
    const structuredOutputText = typeof structuredFinalOutput !== 'undefined' && hasVisibleData(structuredFinalOutput)
        ? formatStructuredOutput(structuredFinalOutput)
        : '';

    const messageRows = useMemo(() => {
        return conversation.messages
            .map((message) => ({
                id: message.id,
                role: message.role.toLowerCase(),
                label: getConversationMessageLabel(message),
                text: getConversationMessagePreviewText(message),
            }))
            .filter((message) => message.text.length > 0)
            .slice(-5);
    }, [conversation.messages]);

    const latestMessageText = messageRows[messageRows.length - 1]?.text ?? '';
    const outputText = humanizeAgentMessage(structuredOutputText || conversation.finalOutputText || conversation.outputText || latestMessageText);
    const isBusy = conversation.isRunning || conversation.isStreaming || isActiveStepStatus(stepStatus);
    const statusLabel = conversation.error
        ? 'Needs attention'
        : isBusy
            ? 'Working'
            : outputText
                ? 'Ready'
                : 'Listening';
    const statusVariant: StatusVariant = conversation.error ? 'error' : isBusy ? 'info' : outputText ? 'success' : 'default';

    useEffect(() => {
        if (!conversationId) {
            settledConversationRef.current = null;
            return;
        }

        if (conversation.isRunning || conversation.isStreaming || conversation.isLoading) {
            if (conversation.isRunning || conversation.isStreaming) settledConversationRef.current = null;
            return;
        }

        const hasSettledSignal = conversation.messages.length > 0 || Boolean(conversation.status) || Boolean(outputText);
        if (!hasSettledSignal) return;
        if (settledConversationRef.current === conversationId) return;

        settledConversationRef.current = conversationId;
        void onAgentSettled?.();
    }, [
        conversation.isLoading,
        conversation.isRunning,
        conversation.isStreaming,
        conversation.messages.length,
        conversation.status,
        conversationId,
        onAgentSettled,
        outputText,
    ]);

    return (
        <div className="signal-surface-intelligence mt-4 rounded-lg px-4 py-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex min-w-0 items-start gap-3">
                    <div className="mt-0.5 flex h-9 w-9 flex-none items-center justify-center rounded-lg bg-[var(--card-bg)]">
                        <Bot className="h-4 w-4 text-[var(--intelligence)]" />
                    </div>
                    <div className="min-w-0">
                        <p className="text-sm font-semibold text-[var(--text-primary)]">Agent workspace</p>
                        <p className="mt-0.5 text-xs leading-5 text-[var(--text-secondary)]">
                            {isBusy ? 'The agent is working through this stage now.' : outputText ? 'Latest agent messages are attached here.' : 'Waiting for the agent messages.'}
                        </p>
                    </div>
                </div>
                <Badge variant={statusVariant} className="flow-execution-badge flow-execution-badge-fit">
                    {statusLabel}
                </Badge>
            </div>

            {conversation.isLoading && !outputText ? (
                <div className="mt-3 flex items-center gap-2 rounded-lg bg-[var(--surface-2)] px-3 py-2 text-xs text-[var(--text-secondary)]">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Loading agent messages
                </div>
            ) : null}

            {messageRows.length > 0 ? (
                <div className="mt-4 space-y-2">
                    {messageRows.slice(-3).map((message) => (
                        <div key={message.id} className={cn(
                            'flex gap-2',
                            message.role === 'user' ? 'justify-start' : 'justify-end'
                        )}>
                            <div className={cn(
                                'max-w-[82%] rounded-lg px-3 py-2 text-xs leading-5 shadow-[var(--shadow-xs)]',
                                message.role === 'user'
                                    ? 'rounded-tl-sm bg-[var(--card-bg)] text-[var(--text-secondary)]'
                                    : 'rounded-tr-sm bg-[var(--intelligence-soft)] text-[var(--text-primary)]'
                            )}>
                                <p className="mb-1 flex items-center gap-1 type-eyebrow">
                                    <MessageCircle className="h-3 w-3" />
                                    {message.label}
                                </p>
                                <p className="whitespace-pre-wrap break-words">{truncatePreview(humanizeAgentMessage(message.text), 420)}</p>
                            </div>
                        </div>
                    ))}
                </div>
            ) : outputText ? (
                <div className="mt-3 rounded-lg rounded-tl-sm bg-[var(--card-bg)] px-3 py-2 text-xs leading-5 text-[var(--text-primary)] shadow-[var(--shadow-xs)]">
                    {truncatePreview(outputText, 700)}
                </div>
            ) : !conversation.isLoading ? (
                <div className="mt-3 flex items-center gap-2 rounded-lg bg-[var(--card-bg)] px-3 py-2 text-xs text-[var(--text-secondary)]">
                    <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--intelligence)]" />
                    Waiting for the first agent message.
                </div>
            ) : null}

            {messageRows.length > 1 ? (
                <button
                    type="button"
                    onClick={() => setIsExpanded((prev) => !prev)}
                    className="flow-execution-inline-button mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                >
                    {isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                    {isExpanded ? 'Hide progress' : 'Show progress'}
                </button>
            ) : null}

            {isExpanded ? (
                <div className="mt-3 space-y-2">
                    {messageRows.map((message) => (
                        <div
                            key={message.id}
                            className={cn(
                                'rounded-lg px-3 py-2',
                                message.role === 'user'
                                    ? 'state-badge-info'
                                    : 'bg-[var(--surface-2)]'
                            )}
                        >
                            <p className="type-eyebrow">
                                {message.label}
                            </p>
                            <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-[var(--text-secondary)]">
                                {truncatePreview(message.text, 420)}
                            </p>
                        </div>
                    ))}
                </div>
            ) : null}

            {conversation.error ? (
                <p className="mt-3 text-xs leading-5 text-[var(--state-error)]">
                    Agent progress could not be loaded. Raw step data is still available below.
                </p>
            ) : null}
        </div>
    );
}

function AgentResultCard({ data }: { data: unknown }) {
    const summaryText = getStepSummaryText(data) || formatStructuredOutput(data);

    return (
        <div className="signal-surface-intelligence mt-4 rounded-lg px-4 py-4">
            <div className="flex items-start gap-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--card-bg)]">
                    <Bot className="h-4 w-4 text-[var(--intelligence)]" />
                </div>
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm font-semibold text-[var(--text-primary)]">Agent result</p>
                        <Badge variant="success" className="flow-execution-badge">Ready</Badge>
                    </div>
                    <p className="mt-2 whitespace-pre-wrap break-words rounded-lg rounded-tl-sm bg-[var(--card-bg)] px-3 py-2 text-xs leading-5 text-[var(--text-primary)] shadow-[var(--shadow-xs)]">
                        {truncatePreview(summaryText, 700)}
                    </p>
                </div>
            </div>
        </div>
    );
}
