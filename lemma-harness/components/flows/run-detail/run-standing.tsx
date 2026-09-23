'use client';

// Where this run stands — the first thing the page should say, and the thing it
// has never said.
//
// The product's own promise is "the workflow picks it up, and you get back the
// exact thing that changed — not just an acknowledgement." A page that opens
// with a list of internal step names answers neither half. A run has exactly two
// interesting states to a person:
//
//   still going  → who has it, and if that is you, the thing you have to do
//   finished     → what came out
//
// Everything below this block is the detail behind that answer.

import { CheckCircle, Clock, XCircle } from '@/components/ui/icons';
import { NodeType, type WorkflowNode } from '@/lib/types';
import {
    getDisplayNodeLabel,
    getRunCurrentNodeId,
    type RunCardRun,
} from '../run-format';
import { getRunActor, ActorAvatar } from './run-actor';
import { RunInputForm } from './run-input-form';

type ActiveWait = {
    wait_type?: string;
    node_id?: string;
    payload?: { input_schema?: Record<string, unknown> | null } | null;
} | null;

export function RunStanding({
    run,
    runStatus,
    nodes,
    onSubmitInput,
}: {
    run: RunCardRun;
    runStatus: string;
    nodes: WorkflowNode[];
    onSubmitInput: (nodeId: string, data: Record<string, unknown>) => Promise<void>;
}) {
    const status = runStatus.toUpperCase();
    const activeWait = (run as { active_wait?: ActiveWait })?.active_wait ?? null;
    const currentNodeId = getRunCurrentNodeId(run);
    const currentNode = currentNodeId ? nodes.find((node) => node.id === currentNodeId) || null : null;

    // Waiting on a person: the most important state a workflow has, and the one
    // that most justifies the feature existing. Put the form right here — making
    // someone hunt for the step that wants them is the whole failure mode.
    if (activeWait?.wait_type === 'HUMAN' && currentNode?.type === NodeType.FORM) {
        return (
            <section className="resource-card">
                <header className="mb-3 flex items-start gap-3">
                    <ActorAvatar kind="human" />
                    <div className="min-w-0">
                        <h2 className="text-sm font-medium text-[var(--text-primary)]">Waiting on a person</h2>
                        <p className="mt-0.5 text-xs text-[var(--text-secondary)]">
                            {getDisplayNodeLabel(currentNode)} — the run continues once this is submitted.
                        </p>
                    </div>
                </header>
                <RunInputForm
                    nodeId={currentNode.id}
                    nodes={nodes}
                    schema={activeWait.payload?.input_schema ?? null}
                    onSubmitInput={onSubmitInput}
                    variant="flat"
                />
            </section>
        );
    }

    if (status === 'FAILED') {
        // The error banner already states the reason; this block stays out of
        // its way rather than saying the same thing twice.
        return null;
    }

    if (status === 'CANCELLED' || status === 'CANCELED') {
        return (
            <section className="resource-card flex items-start gap-3">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)]">
                    <XCircle className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                    <h2 className="text-sm font-medium text-[var(--text-primary)]">Stopped</h2>
                    <p className="mt-0.5 text-xs text-[var(--text-secondary)]">This run was cancelled before it finished.</p>
                </div>
            </section>
        );
    }

    if (status === 'COMPLETED' || status === 'SUCCESS' || status === 'SUCCEEDED') {
        // Deliberately just the status. A workflow's last step is usually an END
        // node or a bookkeeping function, so "the run's result" is either empty
        // or arbitrary — the work is in the steps, and hoisting one of them up
        // here would be picking a winner at random.
        return (
            <section className="resource-card flex items-start gap-3">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--state-success)]">
                    <CheckCircle className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                    <h2 className="text-sm font-medium text-[var(--text-primary)]">Finished</h2>
                    <p className="mt-0.5 text-xs text-[var(--text-secondary)]">Every step below ran through to the end.</p>
                </div>
            </section>
        );
    }

    // Still going — say who has it. "Running" alone is what a status column is
    // for; this block exists to name the participant.
    const actor = currentNode ? getRunActor(currentNode, getDisplayNodeLabel(currentNode)) : null;
    const waitingOnMachine = activeWait?.wait_type === 'AGENT' || activeWait?.wait_type === 'FUNCTION';

    return (
        <section className="resource-card flex items-start gap-3">
            {actor ? <ActorAvatar kind={actor.kind} /> : (
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-2)] text-[var(--text-secondary)]">
                    <Clock className="h-4 w-4" />
                </span>
            )}
            <div className="min-w-0">
                <h2 className="text-sm font-medium text-[var(--text-primary)]">
                    {actor ? <>{actor.name} is working</> : 'Running'}
                </h2>
                <p className="mt-0.5 text-xs text-[var(--text-secondary)]">
                        {activeWait?.wait_type === 'TIME'
                            ? 'Waiting for a scheduled time before it continues.'
                            : waitingOnMachine
                                ? 'The run continues on its own when this finishes.'
                            : 'Working through the steps below.'}
                </p>
            </div>
        </section>
    );
}
