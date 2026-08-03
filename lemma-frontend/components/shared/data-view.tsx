'use client';

// A payload, rendered as a document instead of as code.
//
// The rule was "show JSON nicely wherever we cannot show anything truly better".
// Rendering *everything* as JSON read that as a default; it is a fallback. A
// workflow step's output is usually somebody's actual content — a draft, a
// rating, a note, a set of suggested edits — and `{"note": "Rated the draft
// REVISE…"}` shows the envelope instead of the letter.
//
// This is not the lossy field-grid that used to live here. That one took the
// first eight keys, flattened each to a string, and dropped the rest. This
// renders *everything*: nested records recurse, arrays of records become
// sections, and anything genuinely opaque still falls through to JsonView. The
// raw payload stays one click away at the call site, so nothing is hidden —
// only reordered into something a person can read.

import { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { JsonView } from '@/components/shared/json-view';
import { describeJsonValue } from '@/lib/json/json-payload';
import { humanizeKey } from '@/components/lemma/assistant/assistant-format';

/** Keys that conventionally hold the human-facing answer, most specific first.
 * Promoted above the other fields — never instead of them. */
const LEAD_TEXT_KEYS = ['note', 'summary', 'answer', 'message', 'text', 'content', 'description', 'result'];

const MAX_INLINE_DEPTH = 2;
/** Past this many characters a value stops being a field and starts being a
 * document — it gets clamped with a way to open it, never cut off. */
const LONG_TEXT_CHARS = 420;

function isRecord(value: unknown): value is Record<string, unknown> {
    return !!value && typeof value === 'object' && !Array.isArray(value);
}

function isScalar(value: unknown): boolean {
    return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean';
}

/** The field that reads as this payload's headline, if it has one. */
export function findLeadText(value: unknown): { key: string; text: string } | null {
    if (!isRecord(value)) return null;

    for (const key of LEAD_TEXT_KEYS) {
        const candidate = value[key];
        if (typeof candidate === 'string' && candidate.trim()) {
            return { key, text: candidate.trim() };
        }
    }

    // No conventional key — a single long string field still reads as the answer.
    const longStrings = Object.entries(value).filter(
        ([, entry]) => typeof entry === 'string' && entry.trim().length > 60
    );
    if (longStrings.length === 1) {
        return { key: longStrings[0][0], text: (longStrings[0][1] as string).trim() };
    }
    return null;
}

export function DataView({
    value,
    depth = 0,
    className,
    omitKeys,
}: {
    value: unknown;
    depth?: number;
    className?: string;
    /** Fields already shown elsewhere — a promoted headline, usually. */
    omitKeys?: string[];
}) {
    const renderable = useMemo(() => describeJsonValue(value), [value]);
    if (!renderable) return null;

    if (renderable.kind === 'text') {
        return <ProseValue text={renderable.text} className={className} />;
    }

    if (renderable.kind === 'scalar') {
        return <p className={cn('text-sm text-[var(--text-primary)]', className)}>{renderable.text}</p>;
    }

    // Past a couple of levels the shape is the information, and a document
    // rendering of it stops helping.
    if (depth > MAX_INLINE_DEPTH) {
        return <JsonView value={value} density="compact" className={className} />;
    }

    if (Array.isArray(value)) {
        return (
            <div className={cn('grid gap-2', className)}>
                {value.map((item, index) => (
                    <div key={index} className={cn(isRecord(item) && 'data-view-item')}>
                        <DataView value={item} depth={depth + 1} />
                    </div>
                ))}
            </div>
        );
    }

    if (!isRecord(value)) return <JsonView value={value} density="compact" className={className} />;

    const omitted = new Set(omitKeys || []);
    const entries = Object.entries(value).filter(([key, entry]) => {
        if (omitted.has(key)) return false;
        return describeJsonValue(entry) !== null;
    });
    if (entries.length === 0) return null;

    return (
        <dl className={cn('data-view', className)}>
            {entries.map(([key, entry]) => (
                <div key={key} className={cn('data-view-row', !isScalar(entry) && 'data-view-row-block')}>
                    <dt className="data-view-key">{humanizeKey(key)}</dt>
                    <dd className="data-view-value">
                        <DataView value={entry} depth={depth + 1} />
                    </dd>
                </div>
            ))}
        </dl>
    );
}


/**
 * A text value, read rather than dumped.
 *
 * Two things were wrong with printing the raw string. Agents write markdown, so
 * `**Rating: REVISE.**` arrived with its asterisks showing. And a long answer
 * ran to whatever length it wanted inside a step row — the container's response
 * was a fixed max height with `overflow: hidden`, which silently cut the answer
 * off mid-sentence. Clamped-with-a-toggle is the honest version: you can see
 * there is more, and you can get to it.
 */
export function ProseValue({ text, className }: { text: string; className?: string }) {
    const isLong = text.length > LONG_TEXT_CHARS;
    const [expanded, setExpanded] = useState(false);

    return (
        <div className={cn('min-w-0', className)}>
            <div
                className={cn(
                    'data-view-prose text-sm leading-relaxed text-[var(--text-primary)]',
                    isLong && !expanded && 'data-view-prose-clamped'
                )}
            >
                <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
                    {text}
                </ReactMarkdown>
            </div>
            {isLong ? (
                <Button
                    type="button"
                    variant="quiet"
                    size="sm"
                    className="mt-0.5 h-6 px-1.5 text-xs font-normal text-[var(--action-primary)]"
                    onClick={() => setExpanded((current) => !current)}
                    aria-expanded={expanded}
                >
                    {expanded ? 'Show less' : 'Show more'}
                </Button>
            ) : null}
        </div>
    );
}
