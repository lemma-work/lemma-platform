'use client';

// A run, shaped like the workflow it is.
//
// The first version of this was a flat log, and a flat log throws away the one
// thing a workflow *is*: structure. You could not see that a decision chose one
// branch over another — the branch it rejected was exiled to a "Not reached"
// list at the bottom, next to steps that simply had not happened yet. A loop ran
// three times and read as three unrelated rows.
//
// So this walks the same nested tree the editor walks — `parseDefinition` gives
// decisions their branches and loops their bodies — and paints run state onto
// it. A branch the run did not take stays exactly where the decision is, dimmed
// and labelled, because *that* is the interesting fact about it.

import { useMemo, useState } from 'react';
import { GitBranch, Repeat } from '@/components/ui/icons';
import { cn } from '@/lib/utils';
import { JsonView } from '@/components/shared/json-view';
import type { WorkflowNode } from '@/lib/types';
import { parseDefinition } from '../flow-graph-ops';
import type { StepBranch, StepNode } from '../flow-editor-types';
import {
    getRunTraceEntries,
    isTerminalRunStatus,
    type ProcedureStepState,
    type RunCardRun,
    type RunTraceEntry,
    type WorkflowEdgeLike,
} from '../run-format';
import { RunLogRow, RunSkippedRow } from './run-log-row';

/** A step that failed, is running, or wants something from you opens itself —
 * those are the three reasons anybody opened the page. */
function opensByDefault(state: ProcedureStepState): boolean {
    return state === 'failed' || state === 'running' || state === 'waiting';
}

export function RunTree({
    podId,
    run,
    runStatus,
    nodes,
    edges,
    currentNodeId,
    onRunRefresh,
    onSubmitInput,
}: {
    podId: string;
    run: RunCardRun;
    runStatus: string;
    nodes: WorkflowNode[];
    edges: WorkflowEdgeLike[];
    currentNodeId: string | null;
    onRunRefresh?: () => Promise<void> | void;
    onSubmitInput: (nodeId: string, data: Record<string, unknown>) => Promise<void>;
}) {
    const [overrides, setOverrides] = useState<Map<string, boolean>>(() => new Map());

    // The editor's own model of this workflow. Same call, same tree — a run and
    // an edit should not disagree about the shape of the thing.
    const steps = useMemo(
        () => parseDefinition({ nodes, edges: edges as never, viewport: undefined as never }),
        [nodes, edges]
    );

    const entries = useMemo(
        () => getRunTraceEntries(run, nodes, currentNodeId, runStatus),
        [run, nodes, currentNodeId, runStatus]
    );

    // One node can appear many times — a loop body, a step revisited. Keep every
    // occurrence, in order, so an iteration is a row rather than a footnote.
    const tracesByNode = useMemo(() => {
        const map = new Map<string, RunTraceEntry[]>();
        for (const entry of entries) {
            const bucket = map.get(entry.nodeId);
            if (bucket) bucket.push(entry);
            else map.set(entry.nodeId, [entry]);
        }
        return map;
    }, [entries]);

    const isFinished = isTerminalRunStatus(runStatus);

    const toggle = (key: string, fallbackOpen: boolean) => {
        setOverrides((current) => {
            const next = new Map(current);
            next.set(key, !(current.get(key) ?? fallbackOpen));
            return next;
        });
    };

    const executionContext = (run as { execution_context?: unknown })?.execution_context;

    return (
        <section className="resource-card">
            <div className="mb-3 flex items-baseline gap-2">
                <h4 className="text-sm font-medium text-[var(--text-primary)]">Steps</h4>
                <span className="text-xs text-[var(--text-tertiary)]">in the order they ran</span>
            </div>
            <div className="flow-run-tree lemma-index-list">
            <RunStepList
                steps={steps}
                depth={0}
                podId={podId}
                run={run}
                runStatus={runStatus}
                nodes={nodes}
                isFinished={isFinished}
                tracesByNode={tracesByNode}
                overrides={overrides}
                onToggle={toggle}
                onRunRefresh={onRunRefresh}
                onSubmitInput={onSubmitInput}
            />

            </div>

            {/* The flat view every workflow expression resolves against
                (`<node_id>.<field>`, `start.*`, `loop.*`). Closed by default —
                the first thing worth reading when a binding produced something
                unexpected, and noise the rest of the time. */}
            <JsonView
                value={executionContext}
                label="Run context"
                density="comfortable"
                defaultExpanded={false}
                className="mt-3"
            />
        </section>
    );
}

/** Everything the recursion carries except the steps it is currently drawing. */
type TreeContext = {
    depth: number;
    podId: string;
    run: RunCardRun;
    runStatus: string;
    nodes: WorkflowNode[];
    isFinished: boolean;
    tracesByNode: Map<string, RunTraceEntry[]>;
    overrides: Map<string, boolean>;
    onToggle: (key: string, fallbackOpen: boolean) => void;
    onRunRefresh?: () => Promise<void> | void;
    onSubmitInput: (nodeId: string, data: Record<string, unknown>) => Promise<void>;
};

type ListProps = TreeContext & { steps: StepNode[]; insideUntakenBranch?: boolean };

function RunStepList(props: ListProps) {
    const { steps, insideUntakenBranch = false, ...rest } = props;
    return (
        <>
            {steps.map((step) => (
                <RunStepBlock key={step.id} step={step} insideUntakenBranch={insideUntakenBranch} {...rest} />
            ))}
        </>
    );
}

/** Did any part of this subtree actually run? Decides whether a branch reads as
 * "not taken" or merely "not yet". */
function subtreeWasReached(step: StepNode, tracesByNode: Map<string, RunTraceEntry[]>): boolean {
    if ((tracesByNode.get(step.id)?.length ?? 0) > 0) return true;
    if (step.branches?.some((branch) => branch.steps.some((child) => subtreeWasReached(child, tracesByNode)))) return true;
    return Boolean(step.loopSteps?.some((child) => subtreeWasReached(child, tracesByNode)));
}

function RunStepBlock({ step, depth, insideUntakenBranch = false, ...rest }: TreeContext & { step: StepNode; insideUntakenBranch?: boolean }) {
    const { tracesByNode, nodes, isFinished, overrides, onToggle } = rest;
    const occurrences = tracesByNode.get(step.id) || [];
    const node = nodes.find((candidate) => candidate.id === step.id) || null;
    // Which arm the run actually went down. The decision's own output only says
    // `matched_condition`, which is null for the default path — the trace is the
    // one place that knows, so read it here and let the row say it in words.
    const takenBranch = step.branches?.find((branch) =>
        branch.steps.some((child) => subtreeWasReached(child, tracesByNode))
    ) || null;

    return (
        <div>
            {occurrences.length === 0 ? (
                <RunSkippedRow
                    label={step.label}
                    node={node}
                    // Silent inside a branch already labelled "not taken" —
                    // saying it on the header and on every row under it made
                    // the untaken half the noisiest thing on screen.
                    reason={insideUntakenBranch ? '' : isFinished ? 'Not taken' : 'Not reached'}
                />
            ) : (
                occurrences.map((entry) => {
                    const fallbackOpen = opensByDefault(entry.state);
                    return (
                        <RunLogRow
                            key={entry.key}
                            podId={rest.podId}
                            entry={entry}
                            nodes={nodes}
                            run={rest.run}
                            runStatus={rest.runStatus}
                            isExpanded={overrides.get(entry.key) ?? fallbackOpen}
                            takenBranchLabel={takenBranch ? branchLabel(takenBranch) : null}
                            onToggle={() => onToggle(entry.key, fallbackOpen)}
                            onRunRefresh={rest.onRunRefresh}
                            onSubmitInput={rest.onSubmitInput}
                        />
                    );
                })
            )}

            {step.branches?.length ? (
                <div className="flow-run-tree-children">
                    {step.branches.map((branch) => (
                        <RunBranch
                            key={branch.id}
                            branch={branch}
                            depth={depth + 1}
                            {...rest}
                        />
                    ))}
                </div>
            ) : null}

            {step.loopSteps?.length ? (
                <div className="flow-run-tree-children">
                    <p className="flow-run-tree-label">
                        <Repeat className="h-3 w-3" aria-hidden />
                        Each item
                    </p>
                    <RunStepList {...rest} depth={depth + 1} steps={step.loopSteps} />
                </div>
            ) : null}
        </div>
    );
}

const GENERIC_BRANCH_LABEL = /^branch \d+$/i;

function branchLabel(branch: StepBranch): string {
    const label = branch.label?.trim() || '';
    if (label && !GENERIC_BRANCH_LABEL.test(label)) return label;
    const condition = branch.condition?.trim();
    return condition || 'Otherwise';
}

function RunBranch({ branch, depth, ...rest }: TreeContext & { branch: StepBranch }) {
    const taken = branch.steps.some((child) => subtreeWasReached(child, rest.tracesByNode));

    return (
        <section className={cn('flow-run-tree-branch', !taken && 'flow-run-tree-branch-untaken')}>
            <p className="flow-run-tree-label">
                <GitBranch className="h-3 w-3" aria-hidden />
                {/* `parseDefinition` invents "Branch 1/2/3" for graphs that never
                    stored a label. The condition is the thing that actually
                    distinguishes one path from another. */}
                <span className="truncate font-mono text-[0.75em]">{branchLabel(branch)}</span>
                {/* The fact worth stating about a branch is whether the run went
                    down it — not repeated per step inside it. */}
                {taken ? null : (
                    <span className="flow-run-tree-untaken-tag">
                        {rest.isFinished ? 'not taken' : 'not taken yet'}
                    </span>
                )}
            </p>
            <RunStepList {...rest} depth={depth} steps={branch.steps} insideUntakenBranch={!taken} />
        </section>
    );
}
