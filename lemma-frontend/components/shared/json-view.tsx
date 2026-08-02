'use client';

// The one way this product shows structured data it cannot show better.
//
// The rule it exists to enforce: bespoke rendering only where it genuinely beats
// the payload — a transcript, a form, prose. Everything else goes here, whole.
// What this replaces guessed instead: it took the first eight keys, flattened
// each value to a string, and dropped the rest, which looks like a considered
// summary and is strictly a lossy one.
//
// Degenerate shapes are handled here rather than at each call site, so no caller
// has to ask "is this empty / a string / a number" before deciding to render.

import { useMemo, useState } from 'react';
import { Check, ChevronDown, Code, Copy } from '@/components/ui/icons';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
    describeJsonValue,
    tokenizeJson,
    type JsonPayload,
    type JsonTokenKind,
} from '@/lib/json/json-payload';

/** Longer payloads open collapsed so one step's output cannot bury the page. */
const COLLAPSED_LINE_LIMIT = 24;
const PREVIEW_CHARS = 120;

const TOKEN_CLASS_NAMES: Record<JsonTokenKind, string> = {
    key: 'text-[var(--action-primary)]',
    string: 'text-[var(--state-success)]',
    number: 'text-[var(--state-info)]',
    boolean: 'text-[var(--state-warning)]',
    null: 'text-[var(--text-tertiary)]',
    punctuation: 'text-[var(--text-tertiary)]',
    plain: '',
};

export interface JsonViewProps {
    /** Anything: objects, arrays, scalars, strings, null. */
    value: unknown;
    /** Optional eyebrow above the payload — "Input", "Output", "Run context". */
    label?: string;
    /** `compact` drops the outer chrome for use inside a log row. */
    density?: 'comfortable' | 'compact';
    /** Above this many lines the block opens collapsed. */
    collapsedLineLimit?: number;
    /** Scroll past this height rather than pushing the page. */
    maxHeightClassName?: string;
    /** Force the initial expansion state — a failed step opens its error. */
    defaultExpanded?: boolean;
    /** Palette override for surfaces that own their colors (a chat bubble). */
    monochrome?: boolean;
    className?: string;
}

/**
 * Renders `value` as the thing it actually is. Returns null when there is
 * nothing to say — null, undefined, empty string, `{}`, `[]` — so callers can
 * drop the whole section by rendering this and checking nothing.
 */
export function JsonView({
    value,
    label,
    density = 'comfortable',
    collapsedLineLimit = COLLAPSED_LINE_LIMIT,
    maxHeightClassName = 'max-h-96',
    defaultExpanded,
    monochrome = false,
    className,
}: JsonViewProps) {
    const renderable = useMemo(() => describeJsonValue(value), [value]);

    if (!renderable) return null;

    if (renderable.kind === 'text') {
        return (
            <LabelledValue label={label} density={density} className={className}>
                <p className="whitespace-pre-wrap break-words text-sm leading-6 text-[var(--text-primary)]">
                    {renderable.text}
                </p>
            </LabelledValue>
        );
    }

    if (renderable.kind === 'scalar') {
        return (
            <LabelledValue label={label} density={density} className={className}>
                <p className="font-mono text-sm text-[var(--text-primary)]">{renderable.text}</p>
            </LabelledValue>
        );
    }

    return (
        <JsonBlock
            payload={renderable.payload}
            label={label}
            density={density}
            collapsedLineLimit={collapsedLineLimit}
            maxHeightClassName={maxHeightClassName}
            defaultExpanded={defaultExpanded}
            monochrome={monochrome}
            className={className}
        />
    );
}

function LabelledValue({
    label,
    density,
    className,
    children,
}: {
    label?: string;
    density: 'comfortable' | 'compact';
    className?: string;
    children: React.ReactNode;
}) {
    return (
        <div className={cn(density === 'comfortable' && 'py-1', className)}>
            {label ? <p className="mb-1 type-eyebrow">{label}</p> : null}
            {children}
        </div>
    );
}

function JsonBlock({
    payload,
    label,
    density,
    collapsedLineLimit,
    maxHeightClassName,
    defaultExpanded,
    monochrome,
    className,
}: {
    payload: JsonPayload;
    label?: string;
    density: 'comfortable' | 'compact';
    collapsedLineLimit: number;
    maxHeightClassName: string;
    defaultExpanded?: boolean;
    monochrome: boolean;
    className?: string;
}) {
    const [isExpanded, setIsExpanded] = useState(
        defaultExpanded ?? payload.lineCount <= collapsedLineLimit
    );
    const [copied, setCopied] = useState(false);
    const tokens = useMemo(() => tokenizeJson(payload.formatted), [payload.formatted]);

    const handleCopy = async () => {
        try {
            await navigator.clipboard.writeText(payload.formatted);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch { /* clipboard access denied */ }
    };

    // Inside a surface that owns its palette (a user's chat bubble) structure
    // carries the block and the syntax colors sit this one out.
    const borderClassName = monochrome
        ? 'border-[color:color-mix(in_srgb,var(--text-on-brand)_30%,transparent)]'
        : 'border-[color:var(--row-border)]';
    const surfaceClassName = monochrome
        ? 'bg-[color:color-mix(in_srgb,var(--text-on-brand)_12%,transparent)]'
        : 'bg-[color:color-mix(in_srgb,var(--surface-2)_50%,transparent)]';
    const headerTextClassName = monochrome ? 'text-current' : 'text-[var(--text-secondary)]';

    return (
        <div
            className={cn(
                'overflow-hidden rounded-md border',
                density === 'comfortable' ? 'my-3 first:mt-0 last:mb-0' : 'my-1.5',
                borderClassName,
                surfaceClassName,
                className
            )}
        >
            <div className={cn('flex items-center gap-2 px-2.5 py-1 text-xs', headerTextClassName)}>
                <Code className="size-3.5 shrink-0" aria-hidden="true" />
                <span className="font-medium">{label || 'JSON'}</span>
                <span className="truncate opacity-70">{payload.summary}</span>
                <span className="ml-auto flex shrink-0 items-center gap-0.5">
                    <Button
                        type="button"
                        variant="quiet"
                        size="icon"
                        className="size-6 text-current"
                        onClick={handleCopy}
                        title="Copy JSON"
                        aria-label="Copy JSON"
                    >
                        {copied
                            ? <Check className={cn('size-3.5', monochrome ? 'text-current' : 'text-[var(--state-success)]')} aria-hidden="true" />
                            : <Copy className="size-3.5" aria-hidden="true" />}
                    </Button>
                    <Button
                        type="button"
                        variant="quiet"
                        size="icon"
                        className="size-6 text-current"
                        onClick={() => setIsExpanded((current) => !current)}
                        aria-expanded={isExpanded}
                        title={isExpanded ? 'Collapse JSON' : `Expand JSON (${payload.lineCount} lines)`}
                        aria-label={isExpanded ? 'Collapse JSON' : 'Expand JSON'}
                    >
                        <ChevronDown className={cn('size-3.5 transition-transform', !isExpanded && '-rotate-90')} aria-hidden="true" />
                    </Button>
                </span>
            </div>
            {isExpanded ? (
                <pre className={cn('overflow-auto px-3 pb-2.5 pt-0.5 font-mono text-xs leading-5', maxHeightClassName)}>
                    <code>
                        {tokens.map((token, index) => (
                            token.kind === 'plain'
                                ? token.text
                                : (
                                    <span
                                        key={`${index}-${token.kind}`}
                                        className={monochrome ? 'text-current' : TOKEN_CLASS_NAMES[token.kind]}
                                    >
                                        {token.text}
                                    </span>
                                )
                        ))}
                    </code>
                </pre>
            ) : (
                <Button
                    type="button"
                    variant="quiet"
                    size="sm"
                    onClick={() => setIsExpanded(true)}
                    className="h-auto w-full justify-start gap-2 px-3 pb-2 pt-0.5 font-mono text-xs font-normal"
                >
                    <span className="min-w-0 flex-1 truncate text-left opacity-80">{previewOf(payload)}</span>
                    <span className="shrink-0 opacity-70">{payload.lineCount} lines</span>
                </Button>
            )}
        </div>
    );
}

function previewOf(payload: JsonPayload): string {
    const compact = payload.raw.replace(/\s+/g, ' ').trim();
    return compact.length > PREVIEW_CHARS ? `${compact.slice(0, PREVIEW_CHARS)}…` : compact;
}
