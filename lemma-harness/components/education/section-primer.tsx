'use client';

import Link from 'next/link';
import { X } from '@/components/ui/icons';

import { ProductIcon } from '@/components/pod/product-icon';
import { getConcept, type ConceptId } from '@/lib/education/concepts';
import { useEducationEnabled } from '@/lib/education/use-education-audience';
import { useSectionPrimer } from '@/lib/education/use-education-state';
import { cn } from '@/lib/utils';

interface SectionPrimerProps {
    concept: ConceptId;
    className?: string;
}

/**
 * A slim, dismissible one-line hint. The full explainer lives on demand in the
 * header ConceptHint (ⓘ) popover, so this stays out of the way — and reads fine
 * even when the page is embedded in a narrow side-panel next to a conversation.
 */
export function SectionPrimer({ concept, className }: SectionPrimerProps) {
    const entry = getConcept(concept);
    const enabled = useEducationEnabled();
    const { visible, dismiss } = useSectionPrimer(`primer:${concept}`);

    if (!enabled || !visible) return null;

    return (
        <section
            aria-label={`About ${entry.term.toLowerCase()}s`}
            className={cn('surface-panel flex items-center gap-2.5 px-3.5 py-2', className)}
        >
            <ProductIcon kind={entry.iconKind} size="xs" />
            <p className="min-w-0 flex-1 text-xs leading-5 text-[var(--text-secondary)]">
                <span className="font-medium text-[var(--text-primary)]">{entry.term}s: </span>
                {entry.oneLiner}
            </p>
            <Link
                href={`/docs/${entry.guideSlug}`}
                target="_blank"
                rel="noopener noreferrer"
                className="shrink-0 text-xs font-medium text-[var(--text-secondary)] transition-gentle hover:text-[var(--text-primary)] focus-ring"
            >
                Learn more
            </Link>
            <button
                type="button"
                aria-label="Dismiss"
                onClick={dismiss}
                className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[var(--text-tertiary)] transition-gentle hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)] focus-ring"
            >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
        </section>
    );
}
