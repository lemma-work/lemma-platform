'use client';

// What a step actually did, said for the person who set the workflow up.
//
// The test this is written against: can somebody in marketing or sales read
// this page and understand what happened? Against a screen of `{"note": "…",
// "contributions": [{"kind": "comment", "impact": 1, …}]}` the answer is no —
// and "matched_condition: null" tells them nothing at all about which way a
// decision went, when the branch it chose has a name right there in the graph.
//
// So each step type gets the reading that suits it, and the raw payload moves
// behind a toggle. Nothing is hidden: "Show data" is always there, one click,
// and it is the same JsonView as before.

import { useState } from 'react';
import Link from 'next/link';
import { ArrowUpRight, Code, MessageCircle } from '@/components/ui/icons';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { JsonView } from '@/components/shared/json-view';
import { DataView, findLeadText } from '@/components/shared/data-view';
import { hasRenderableJson } from '@/lib/json/json-payload';
import { NodeType, type WorkflowNode } from '@/lib/types';

/** A quiet escape hatch to the payload. The document view is the default, not
 * the whole story — anything it reorders stays available verbatim. */
export function RawDataToggle({ label = 'Show data', value }: { label?: string; value: unknown }) {
    const [open, setOpen] = useState(false);
    if (!hasRenderableJson(value)) return null;

    return (
        <div className="pt-1">
            <Button
                type="button"
                variant="quiet"
                size="sm"
                className="h-6 gap-1.5 px-1.5 text-xs font-normal text-[var(--text-tertiary)]"
                onClick={() => setOpen((current) => !current)}
                aria-expanded={open}
            >
                <Code className="h-3 w-3" />
                {open ? 'Hide data' : label}
            </Button>
            {open ? <JsonView value={value} density="compact" defaultExpanded /> : null}
        </div>
    );
}

/** A form submission is a set of answers — labels and values, which is what the
 * person filling it in saw. */
export function FormStepBody({ submitted }: { submitted: unknown }) {
    if (!hasRenderableJson(submitted)) {
        return <p className="text-sm text-[var(--text-tertiary)]">Nothing was submitted.</p>;
    }
    return (
        <>
            <DataView value={submitted} />
            <RawDataToggle value={submitted} />
        </>
    );
}

/**
 * An agent's answer, then its workings.
 *
 * The headline is the agent's own sentence — not a paraphrase of it — with the
 * rest of the payload below and the conversation a click away. The transcript
 * used to be the only way to see any of this, which meant a step whose
 * conversation could not be resolved showed nothing at all.
 */
export function AgentStepBody({
    podId,
    input,
    output,
    conversationId,
    transcriptSlot,
}: {
    podId: string;
    input?: unknown;
    output: unknown;
    conversationId: string | null;
    transcriptSlot?: React.ReactNode;
}) {
    const lead = findLeadText(output);

    return (
        <>
            {hasRenderableJson(input) ? (
                <section>
                    <p className="type-eyebrow mb-1">Asked to</p>
                    <DataView value={input} />
                </section>
            ) : null}
            {lead ? (
                <p className="whitespace-pre-wrap break-words text-sm leading-6 text-[var(--text-primary)]">
                    {lead.text}
                </p>
            ) : null}

            <DataView value={output} omitKeys={lead ? [lead.key] : undefined} />

            {conversationId ? (
                <Link
                    href={`/pod/${podId}/conversations/${encodeURIComponent(conversationId)}`}
                    className="custom-focus-ring inline-flex w-fit items-center gap-1.5 rounded-md text-xs font-medium text-[var(--action-primary)] hover:underline"
                >
                    <MessageCircle className="h-3.5 w-3.5" />
                    Open the conversation
                    <ArrowUpRight className="h-3 w-3" />
                </Link>
            ) : null}

            {transcriptSlot}
            <RawDataToggle value={output} />
        </>
    );
}

/**
 * Which way a decision went, in the workflow's own words.
 *
 * `{"matched_condition": null}` is the truth and it is useless: null means no
 * condition matched, i.e. the default arm. The branch has a name; say the name.
 */
export function DecisionStepBody({
    output,
    takenBranchLabel,
}: {
    output: unknown;
    takenBranchLabel: string | null;
}) {
    const matched = typeof (output as Record<string, unknown>)?.matched_condition === 'string'
        ? String((output as Record<string, unknown>).matched_condition)
        : null;

    return (
        <>
            <p className="text-sm text-[var(--text-primary)]">
                {takenBranchLabel
                    ? <>Continued down <span className="font-medium">{takenBranchLabel}</span>.</>
                    : 'Continued to the next step.'}
                {matched ? (
                    <span className="text-[var(--text-tertiary)]"> Matched <code className="font-mono text-xs">{matched}</code>.</span>
                ) : (
                    <span className="text-[var(--text-tertiary)]"> No condition matched, so it took the default path.</span>
                )}
            </p>
            <RawDataToggle value={output} />
        </>
    );
}

/** Everything else: inputs and outputs, as a document. */
export function GenericStepBody({
    input,
    output,
    outputLabel = 'Result',
}: {
    input: unknown;
    output: unknown;
    outputLabel?: string;
}) {
    const hasInput = hasRenderableJson(input);
    const hasOutput = hasRenderableJson(output);

    if (!hasInput && !hasOutput) {
        return <p className="text-sm text-[var(--text-tertiary)]">This step recorded no input or output.</p>;
    }

    return (
        <>
            {hasOutput ? (
                <section>
                    <p className="type-eyebrow mb-1">{outputLabel}</p>
                    <DataView value={output} />
                </section>
            ) : null}
            {hasInput ? (
                <section className={cn(hasOutput && 'pt-1')}>
                    <p className="type-eyebrow mb-1">Input</p>
                    <DataView value={input} />
                </section>
            ) : null}
            <RawDataToggle value={hasOutput ? output : input} />
        </>
    );
}

export function stepOutputLabel(type: WorkflowNode['type'] | undefined): string {
    if (type === NodeType.END) return 'Final result';
    if (type === NodeType.FUNCTION) return 'Result';
    return 'Output';
}
