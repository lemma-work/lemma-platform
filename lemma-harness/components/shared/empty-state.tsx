import type { ReactNode } from 'react';

import { getConcept, type ConceptId } from '@/lib/education/concepts';
import { cn } from '@/lib/utils';

/**
 * Empty is a fill, not a screen.
 *
 * There used to be six components here — `EmptyState` with three variants at
 * `py-5` / `py-10` / `py-24`, plus `InlineEmptyState`, `QuietEmptyState`,
 * `SidebarEmptyState` and `RecoveryState` — with no rule for which went where,
 * and single screens mixing three of them. Worse, the panel variant drew a
 * *dashed* border while the settled content drew solid cards, so the container's
 * own outline changed the moment data arrived.
 *
 * Now there is one axis, and it answers a question about the container rather
 * than about the mood:
 *
 * - `inline` — a row inside something that already has a frame: a sidebar, a
 *   panel, a list. Left-aligned, because it sits where a row would.
 * - `region` — a content region that has nothing in it. Centred, solid, and the
 *   same box the skeleton and the settled content occupy.
 * - `page` — the whole route is empty. Centred and large.
 *
 * `QuietEmptyState` survives alongside it for the one-line case where even a box
 * is too much — "Nothing is waiting on you."
 */
export type EmptyStateVariant = 'inline' | 'region' | 'page';

interface EmptyStateProps {
    title: string;
    description?: string;
    /** Teaching fallback: when no description is passed, explain the concept instead. */
    concept?: ConceptId;
    icon?: ReactNode;
    action?: ReactNode;
    variant?: EmptyStateVariant;
    className?: string;
}

export function EmptyState({ title, description, concept, icon, action, variant = 'region', className }: EmptyStateProps) {
    const resolvedDescription = description ?? (concept ? getConcept(concept).oneLiner : '');

    if (variant === 'inline') {
        return (
            <div className={cn('flex items-start gap-3 rounded-md px-2 py-3 text-left', className)}>
                {icon ? (
                    <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[var(--border-subtle)] text-[var(--text-tertiary)]">
                        {icon}
                    </span>
                ) : null}
                <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-[var(--text-primary)]">{title}</p>
                    {resolvedDescription ? (
                        <p className="mt-0.5 text-xs leading-5 text-[var(--text-secondary)]">{resolvedDescription}</p>
                    ) : null}
                </div>
                {action ? <div className="shrink-0">{action}</div> : null}
            </div>
        );
    }

    const isPage = variant === 'page';

    return (
        <div
            className={cn(
                'flex flex-col items-center justify-center text-center',
                // Solid, never dashed. A dashed placeholder box that becomes a
                // solid card on arrival changes the container's outline as well
                // as its contents, which reads as the layout being rebuilt — so
                // this takes the same quiet border the cards take.
                'rounded-lg border border-[color:color-mix(in_srgb,var(--border-subtle)_74%,transparent)] bg-[var(--surface-1)]',
                isPage ? 'px-6 py-24' : 'px-5 py-8',
                className
            )}
        >
            {icon && (
                <div
                    className={cn(
                        'flex items-center justify-center rounded-full border border-[var(--border-subtle)] bg-[var(--surface-1)] text-[var(--text-tertiary)]',
                        isPage ? 'mb-6 h-20 w-20 rounded-2xl' : 'mb-3 h-10 w-10'
                    )}
                >
                    {icon}
                </div>
            )}
            <h3
                className={cn(
                    'font-semibold tracking-normal text-[var(--text-primary)]',
                    isPage ? 'font-display mb-3 text-2xl tracking-tight' : 'mb-1 text-sm'
                )}
            >
                {title}
            </h3>
            <p
                className={cn(
                    'max-w-sm text-[var(--text-secondary)]',
                    isPage ? 'mb-8 text-base leading-relaxed' : 'text-xs leading-5'
                )}
            >
                {resolvedDescription}
            </p>
            {action && <div className={cn(isPage ? 'mt-2' : 'mt-3')}>{action}</div>}
        </div>
    );
}

/**
 * One line, no box. For places where the absence is a fact in a list rather than
 * a state of the screen — "No recent runs yet." under a tab that has four other
 * tabs with content in them.
 */
export function QuietEmptyState({
    icon,
    children,
    className,
}: {
    icon?: ReactNode;
    children: ReactNode;
    className?: string;
}) {
    return (
        <div className={cn('flex items-center gap-2 px-1 py-3 text-sm text-[var(--text-tertiary)]', className)}>
            {icon}
            <span>{children}</span>
        </div>
    );
}
