'use client';

// The one step body that genuinely beats its payload: an agent's conversation.
//
// Everything else a step can produce is JSON and renders as JSON in the log row.
// A transcript is not a payload — it is a sequence of turns with roles, tool
// calls and streaming state — so it keeps the full assistant surface, including
// the composer, because following up mid-run is the point.
//
// This file used to also hold RunPlaybackStep and a stack of chambers built for
// the old two-rail layout. The run log renders those cases directly now.

import { useEffect, useMemo, useRef } from 'react';
import { useAssistantController } from 'lemma-sdk/react';
import { XCircle } from '@/components/ui/icons';
import { cn } from '@/lib/utils';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { AssistantExperienceView } from '@/components/lemma/assistant/assistant-experience';
import type { AssistantControllerView } from '@/components/lemma/assistant/assistant-types';
import { AgentStepBody } from './step-body';
import type { WorkflowNode } from '@/lib/types';
import {
    getNodeAgentName,
    hasVisibleData,
    isActiveStepStatus,
    type ProcedureStepState,
} from '../run-format';
import { StepLoader } from '@/components/brand/loader';

/**
 * A workflow hands its agent the step inputs as the conversation's opening user
 * message, prefixed with this.
 *
 * The transcript then replayed it as a chat bubble: a fake message nobody sent,
 * containing a nested JSON block, above the reply you actually came to read. It
 * is not conversation — it is this step's input. So it comes out of the
 * transcript and goes back in once, at the top, as the input it always was.
 */
const WORKFLOW_INPUT_PREFIX = 'Workflow input JSON:';

function isWorkflowInputMessage(message: { role?: string; content?: string }): boolean {
    return message.role === 'user'
        && typeof message.content === 'string'
        && message.content.trimStart().startsWith(WORKFLOW_INPUT_PREFIX);
}

function unwrapWorkflowInput(value: unknown): unknown {
    if (typeof value !== 'string') return value;
    const trimmed = value.trim();
    if (!trimmed.startsWith(WORKFLOW_INPUT_PREFIX)) return value;
    return trimmed.slice(WORKFLOW_INPUT_PREFIX.length).trim();
}

export function AgentStepChamber({
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

    // When the agent stops, the run has almost certainly advanced — pull the run
    // rather than waiting out the poll interval.
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

    // The bootstrap prompt is lifted out of the transcript and rendered once,
    // above it. Everything else about the conversation is untouched.
    const messages = controller.messages as AssistantControllerView['messages'];
    const bootstrapMessage = useMemo(
        () => messages.find((message) => isWorkflowInputMessage(message)) || null,
        [messages]
    );
    const controllerView = useMemo<AssistantControllerView>(
        () => ({
            ...(controller as unknown as AssistantControllerView),
            messages: bootstrapMessage
                ? messages.filter((message) => message.id !== bootstrapMessage.id)
                : messages,
        }),
        [bootstrapMessage, controller, messages]
    );
    // Mount the conversation surface only when there is a conversation to show.
    //
    // It used to mount whenever the *node* named an agent, which meant a
    // finished step with no reachable transcript rendered ~650px of empty box
    // and a "Follow up with the agent…" composer — on a run that had already
    // ended. An empty chat is not a neutral placeholder; it is the loudest thing
    // on the page, and it says nothing.
    const hasTranscript = controllerView.messages.length > 0;
    const isBusy = controller.isOpenedConversationRunning || controller.isLoading || isActiveStepStatus(stepStatus) || state === 'running';
    const showTranscript = Boolean(conversationId) && (hasTranscript || isBusy);
    const input = useMemo(
        () => unwrapWorkflowInput(bootstrapMessage?.content ?? inputData),
        [bootstrapMessage, inputData]
    );

    return (
        <section className="flex min-h-0 flex-col gap-2">
            {/* The answer, then the workings, then the conversation. The
                transcript is one way to reach an agent's reply, not the only
                one — a step whose conversation cannot be resolved still has an
                answer worth reading. */}
            <AgentStepBody
                podId={podId}
                input={input}
                output={outputData}
                conversationId={conversationId}
                transcriptSlot={showTranscript ? null : (
                    <AgentTranscriptNote
                        state={state}
                        hasConversation={Boolean(conversationId)}
                        hasOutput={hasVisibleData(outputData)}
                    />
                )}
            />

            {/* Only the transcript is bounded, because it scrolls itself. A
                document does not, so bounding it just hides the end. */}
            <div className={cn('min-h-0 flex-1', showTranscript && 'min-h-[16rem] max-h-[30rem] overflow-hidden')}>
                {showTranscript ? (
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
                        placeholder={isBusy ? 'Message the agent while it works...' : 'Follow up with the agent...'}
                        className="h-full min-h-0 rounded-none border-0 bg-transparent shadow-none"
                        contentWidthClassName="max-w-none gap-4"
                        composerWidthClassName="max-w-none"
                        emptyState={(
                            <div className="flex h-full min-h-[180px] items-center justify-center text-sm text-[var(--text-secondary)]">
                                {isBusy ? 'Waiting for the first agent message.' : 'No agent messages yet.'}
                            </div>
                        )}
                    />
                ) : null}
            </div>
        </section>
    );
}


/**
 * One quiet line about a missing transcript — and only when its absence is
 * actually notable. A finished step that recorded its result owes no
 * explanation for not keeping the conversation around.
 */
function AgentTranscriptNote({
    state,
    hasConversation,
    hasOutput,
}: {
    state: ProcedureStepState;
    hasConversation: boolean;
    hasOutput: boolean;
}) {
    if (state === 'failed' && !hasOutput) {
        return (
            <p className="flex items-center gap-2 px-1 py-2 text-sm text-[var(--text-secondary)]">
                <XCircle className="h-4 w-4 shrink-0 text-[var(--state-error)]" />
                This step failed before the agent produced anything.
            </p>
        );
    }

    if (!hasConversation && !hasOutput) {
        return (
            <p className="flex items-center gap-2 px-1 py-2 text-sm text-[var(--text-tertiary)]">
                <StepLoader size="sm" className="text-[var(--text-primary)]" />
                Waiting for the agent to start.
            </p>
        );
    }

    // Runs from before steps recorded what they suspended on cannot find their
    // own conversation. Say so once, quietly, rather than showing an empty chat.
    if (!hasConversation && hasOutput) {
        return (
            <p className="px-1 pt-1 text-xs text-[var(--text-tertiary)]">
                The transcript for this step was not recorded.
            </p>
        );
    }

    return null;
}
