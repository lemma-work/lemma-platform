'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { ArrowRight, Info } from '@/components/ui/icons';

import { ConceptIllustration, hasConceptIllustration } from '@/components/education/concept-illustration';
import { ProductIcon } from '@/components/pod/product-icon';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { getConcept, type ConceptId } from '@/lib/education/concepts';
import { useEducationEnabled } from '@/lib/education/use-education-audience';
import { cn } from '@/lib/utils';

interface ConceptHintProps {
    concept: ConceptId;
    children?: ReactNode;
    side?: 'top' | 'right' | 'bottom' | 'left';
    className?: string;
}

export function ConceptHint({ concept, children, side = 'bottom', className }: ConceptHintProps) {
    const entry = getConcept(concept);
    const enabled = useEducationEnabled();

    // For operators, drop the hint affordance entirely — keep wrapped text, hide the standalone icon.
    if (!enabled) {
        return children ? <>{children}</> : null;
    }

    return (
        <Popover>
            <PopoverTrigger asChild>
                {children ? (
                    <button
                        type="button"
                        aria-label={`Learn about ${entry.term.toLowerCase()}`}
                        className={cn(
                            'cursor-help rounded-sm underline decoration-[color:var(--border-strong)] decoration-dotted underline-offset-4 transition-gentle hover:decoration-[color:var(--text-secondary)] focus-ring',
                            className
                        )}
                    >
                        {children}
                    </button>
                ) : (
                    <button
                        type="button"
                        aria-label={`Learn about ${entry.term.toLowerCase()}`}
                        className={cn(
                            'inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[var(--text-tertiary)] transition-gentle hover:bg-[var(--surface-2)] hover:text-[var(--text-secondary)] focus-ring',
                            className
                        )}
                    >
                        <Info className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                )}
            </PopoverTrigger>
            <PopoverContent side={side} align="start" className="w-80">
                {hasConceptIllustration(concept) ? (
                    <div className="mb-3 rounded-md bg-[var(--bg-subtle)] px-2 py-1">
                        <ConceptIllustration concept={concept} />
                    </div>
                ) : null}
                <div className="flex items-center gap-2">
                    <ProductIcon kind={entry.iconKind} size="sm" />
                    <span className="text-sm font-semibold text-[var(--text-primary)]">{entry.term}</span>
                </div>
                <p className="mt-2 text-xs leading-5 text-[var(--text-primary)]">{entry.oneLiner}</p>
                {entry.explainer[0] ? (
                    <p className="mt-2 text-xs leading-5 text-[var(--text-secondary)]">{entry.explainer[0]}</p>
                ) : null}
                <p className="mt-3 rounded-md bg-[var(--bg-subtle)] px-3 py-2 text-xs leading-5 text-[var(--text-secondary)]">
                    <span className="font-medium text-[var(--text-primary)]">Example: </span>
                    {entry.example}
                </p>
                <Link
                    href={`/docs/${entry.guideSlug}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-[var(--text-secondary)] transition-gentle hover:text-[var(--text-primary)]"
                >
                    Open guide
                    <ArrowRight className="h-3 w-3" aria-hidden="true" />
                </Link>
            </PopoverContent>
        </Popover>
    );
}
