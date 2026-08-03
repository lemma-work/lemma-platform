'use client';

// One event in the run log: a click target that says what happened, and — when
// opened — the payload that proves it.
//
// The dispatch table below is the whole design. A body is bespoke only where it
// genuinely beats the JSON: a conversation, a form, the final result of a run.
// Everything else is the payload, rendered whole, because a summary of a step's
// output that drops half of it is worse than the output.

import { ChevronDown } from '@/components/ui/icons';
import { cn } from '@/lib/utils';
import { JsonView } from '@/components/shared/json-view';
import {
    DecisionStepBody,
    FormStepBody,
    GenericStepBody,
    stepOutputLabel,
} from './step-body';
import { NodeType, type WorkflowNode } from '@/lib/types';
import {
    formatPreciseDuration,
    getDisplayNodeLabel,
    getProcedureStepStatusLabel,
    hasVisibleData,
    parseApiDate,
    type ProcedureStepState,
    type RunCardRun,
    type RunTraceEntry,
} from '../run-format';
import { InlineStepDot } from './step-dots';
import { ActorAvatar, getRunActor } from './run-actor';
import { AgentStepChamber } from './run-steps';
import { RunInputForm } from './run-input-form';

/** A step's own failure reason. Written by `run.fail()` to `step.error` — never
 * to `output_data`, which is why reading only the latter showed nothing. */
function stepError(step: Record<string, unknown> | null): string | null {
    const value = step?.error;
    return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function stepDuration(step: Record<string, unknown> | null, state: ProcedureStepState): string | null {
    if (!step) return null;
    const precise = formatPreciseDuration(step.started_at, step.completed_at);
    if (precise) return precise;
    if (state !== 'running') return null;

    const startedAt = parseApiDate(step.started_at);
    if (!startedAt) return null;
    const elapsed = Math.max(0, Date.now() - startedAt.getTime());
    return elapsed < 1000 ? null : `${Math.round(elapsed / 1000)}s so far`;
}

export function RunLogRow({
    podId,
    entry,
    nodes,
    run,
    runStatus,
    isExpanded,
    takenBranchLabel,
    onToggle,
    onRunRefresh,
    onSubmitInput,
}: {
    podId: string;
    entry: RunTraceEntry;
    nodes: WorkflowNode[];
    run: RunCardRun;
    runStatus: string;
    isExpanded: boolean;
    takenBranchLabel?: string | null;
    onToggle: () => void;
    onRunRefresh?: () => Promise<void> | void;
    onSubmitInput: (nodeId: string, data: Record<string, unknown>) => Promise<void>;
}) {
    const { node, step, state, label } = entry;
    const actor = getRunActor(node, label);
    const error = stepError(step);
    const duration = stepDuration(step, state);
    const activeWait = (run as { active_wait?: { wait_type?: string; node_id?: string; external_ref?: string | null; payload?: { input_schema?: Record<string, unknown> | null } | null } | null })?.active_wait ?? null;
    const isWaitingHere = activeWait?.node_id === entry.nodeId;

    return (
        <div className={cn('lemma-index-row', isExpanded && 'lemma-index-row-selected')}>
            <button
                type="button"
                onClick={onToggle}
                aria-expanded={isExpanded}
                className="custom-focus-ring flex w-full items-center gap-3 px-3 py-2.5 text-left"
            >
                {/* Who did this, not what kind of node it was. An agent's name
                    and a function's name are facts about the run; "Agent" and
                    "Function" are facts about our schema. */}
                <ActorAvatar kind={actor.kind} />
                <span className="min-w-0 flex-1">
                    <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate text-sm font-medium text-[var(--text-primary)]">{actor.name}</span>
                        <span className="shrink-0 text-xs text-[var(--text-tertiary)]">
                            {actor.name === label ? actor.role : label}
                        </span>
                    </span>
                </span>
                <InlineStepDot state={state} active={isExpanded} />
                {entry.occurrence > 1 ? (
                    <span className="chip chip-pill chip-sm chip-muted type-micro-label shrink-0">Run {entry.occurrence}</span>
                ) : null}
                {/* The only thing that earns the right edge is the elapsed time,
                    which wants a column to compare down. Everything else stays
                    next to the name — a log row with its facts a screen away
                    from its label is not scannable. */}
                <span className="w-16 shrink-0 text-right text-xs tabular-nums text-[var(--text-tertiary)]">
                    {duration || (state === 'completed' ? '' : getProcedureStepStatusLabel(state))}
                </span>
                <ChevronDown
                    className={cn('h-4 w-4 shrink-0 text-[var(--text-tertiary)] transition-transform', !isExpanded && '-rotate-90')}
                    aria-hidden="true"
                />
            </button>

            {isExpanded ? (
                <div className="flow-run-log-body">
                    {error ? (
                        <JsonView value={error} label="Error" density="compact" defaultExpanded className="state-surface-error rounded-md px-3 py-2" />
                    ) : null}
                    <RunLogRowBody
                        podId={podId}
                        entry={entry}
                        nodes={nodes}
                        runStatus={runStatus}
                        takenBranchLabel={takenBranchLabel ?? null}
                        isWaitingHere={isWaitingHere}
                        activeWait={activeWait}
                        onRunRefresh={onRunRefresh}
                        onSubmitInput={onSubmitInput}
                    />
                </div>
            ) : null}
        </div>
    );
}

/**
 * The dispatch table. Six cases, closed — anything not named here is its
 * payload.
 */
function RunLogRowBody({
    podId,
    entry,
    nodes,
    runStatus,
    takenBranchLabel,
    isWaitingHere,
    activeWait,
    onRunRefresh,
    onSubmitInput,
}: {
    podId: string;
    entry: RunTraceEntry;
    nodes: WorkflowNode[];
    runStatus: string;
    takenBranchLabel: string | null;
    isWaitingHere: boolean;
    activeWait: { wait_type?: string; node_id?: string; external_ref?: string | null; payload?: { input_schema?: Record<string, unknown> | null } | null } | null;
    onRunRefresh?: () => Promise<void> | void;
    onSubmitInput: (nodeId: string, data: Record<string, unknown>) => Promise<void>;
}) {
    const { node, step, state } = entry;
    const input = step?.input_data;
    const output = step?.output_data;

    // A conversation is not a payload.
    if (node?.type === NodeType.AGENT) {
        // The step's own external_ref is the reliable source: the active wait
        // only exists while the run is suspended *here*, so a finished agent
        // step had nothing to open and rendered an empty transcript.
        const conversationId = stepExternalRef(step)
            ?? (isWaitingHere && activeWait?.wait_type === 'AGENT' ? activeWait.external_ref ?? null : null)
            ?? agentConversationIdFrom(output);

        return (
            <div className="flow-run-log-chamber">
                <AgentStepChamber
                    podId={podId}
                    node={node}
                    stepStatus={String(step?.status || runStatus)}
                    conversationId={conversationId}
                    inputData={input}
                    outputData={output}
                    state={state}
                    onAgentSettled={onRunRefresh}
                />
            </div>
        );
    }

    // An interaction, not data.
    if (node?.type === NodeType.FORM && isWaitingHere && activeWait?.wait_type === 'HUMAN') {
        return (
            <RunInputForm
                nodeId={entry.nodeId}
                nodes={nodes}
                schema={activeWait.payload?.input_schema ?? null}
                onSubmitInput={onSubmitInput}
                variant="flat"
            />
        );
    }

    // A submitted form is a set of answers, not a payload.
    if (node?.type === NodeType.FORM) {
        return <FormStepBody submitted={output} />;
    }

    // A decision's own output says `matched_condition: null`. The branch it took
    // has a name; the tree knows it, so say it.
    if (node?.type === NodeType.DECISION) {
        return <DecisionStepBody output={output} takenBranchLabel={takenBranchLabel} />;
    }

    if (!hasVisibleData(input) && !hasVisibleData(output)) {
        return (
            <p className="px-1 py-2 text-sm text-[var(--text-tertiary)]">
                {state === 'pending' ? 'Not reached in this run.' : 'This step recorded no input or output.'}
            </p>
        );
    }

    return <GenericStepBody input={input} output={output} outputLabel={stepOutputLabel(node?.type)} />;
}

/** What this step suspended on. Recorded at suspend time and untouched by
 * resume, so it is there whether the run is live or long finished. */
function stepExternalRef(step: Record<string, unknown> | null): string | null {
    const value = step?.external_ref;
    return typeof value === 'string' && value ? value : null;
}

/** Runs that predate `step.external_ref` sometimes carried the conversation on
 * the step output; keep reading it so their transcripts still open. */
function agentConversationIdFrom(output: unknown): string | null {
    if (!output || typeof output !== 'object') return null;
    const record = output as Record<string, unknown>;
    for (const key of ['conversation_id', 'agent_conversation_id', 'conversationId']) {
        const value = record[key];
        if (typeof value === 'string' && value) return value;
    }
    return null;
}


/**
 * A step the run did not execute — either not yet, or because a decision went
 * the other way. It stays in position rather than being collected into a list
 * at the bottom: where it sits is the whole point.
 */
export function RunSkippedRow({
    label,
    node,
    reason,
}: {
    label: string;
    node: WorkflowNode | null;
    reason: string;
}) {
    const actor = getRunActor(node, node ? getDisplayNodeLabel(node) : label);

    return (
        <div className="lemma-index-row flex w-full items-center gap-3 px-3 py-2.5 opacity-55">
            <ActorAvatar kind={actor.kind} />
            <span className="min-w-0 flex-1">
                <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm text-[var(--text-secondary)]">{actor.name}</span>
                    <span className="shrink-0 text-xs text-[var(--text-tertiary)]">{actor.role}</span>
                </span>
            </span>
            <InlineStepDot state="pending" active={false} />
            <span className="w-16 shrink-0 text-right text-xs text-[var(--text-tertiary)]">{reason}</span>
            <span className="h-4 w-4 shrink-0" aria-hidden="true" />
        </div>
    );
}
