import { cn } from '@/lib/utils';
import { Skeleton } from './skeleton';

/**
 * The shapes below are *derived*, not drawn. Each one reuses the class names of
 * the component it stands in for — `resource-index-card`, `lemma-index-tabs`,
 * `lemma-index-row` — so its box is computed by the same CSS that will compute
 * the settled box. A skeleton measured by eye is a skeleton that re-flows the
 * page the moment data lands, which is the whole problem.
 *
 * `data-skeleton` switches off the card reveal animation: real cards should
 * animate in when they arrive, placeholders should not animate in first and
 * make you watch the same motion twice.
 */

/** Mirrors an index card — icon, title, two-line summary, footer row. */
export function ResourceCardSkeleton({ className }: { className?: string }) {
    return (
        <div className={cn('resource-index-card min-h-40', className)} data-skeleton="true">
            <div className="flex items-start justify-between gap-3">
                <Skeleton shape="block" className="h-11 w-11 rounded-lg" />
            </div>

            <div className="mt-3 min-w-0">
                <div className="flex h-6 items-center">
                    <Skeleton shape="block" className="h-4 w-32" />
                </div>
                <div className="mt-1 flex min-h-10 flex-col justify-center gap-2">
                    <Skeleton className="h-3 w-full" />
                    <Skeleton className="h-3 w-3/5" />
                </div>
            </div>

            <div className="mt-3 flex h-5 items-center justify-between gap-2">
                <Skeleton className="h-3 w-28" />
                <Skeleton className="h-3 w-14" />
            </div>
        </div>
    );
}

/** A grid of index cards, in the grid the real cards will land in. */
export function ResourceCardGridSkeleton({
    count = 3,
    className,
}: {
    count?: number;
    className?: string;
}) {
    return (
        <div
            className={cn(
                'resource-index-grid resource-index-grid-md-2 resource-index-grid-xl-3 sm:grid-cols-2 xl:grid-cols-3',
                className
            )}
        >
            {Array.from({ length: count }).map((_, index) => (
                <ResourceCardSkeleton key={index} />
            ))}
        </div>
    );
}

/**
 * The metric/tab strip, held at its settled height.
 *
 * In practice this is rarely the right answer: a strip whose labels are known
 * ahead of the data should render its real labels with `—` for the counts, so
 * the row never changes width. Use this only where the tabs themselves are
 * data-dependent.
 */
export function MetricStripSkeleton({
    count = 3,
    className,
}: {
    count?: number;
    className?: string;
}) {
    return (
        <div className={cn('lemma-index-tabs flex-wrap', className)} data-skeleton="true">
            <div className="flex flex-wrap items-center gap-2">
                {Array.from({ length: count }).map((_, index) => (
                    <span key={index} className="lemma-index-tab">
                        <Skeleton className="h-3 w-16" />
                    </span>
                ))}
            </div>
        </div>
    );
}

/** One dense list line — an icon slot, a label, a trailing value. */
export function ListRowSkeleton({
    widthClassName = 'w-3/5',
    trailing = true,
    className,
}: {
    widthClassName?: string;
    trailing?: boolean;
    className?: string;
}) {
    return (
        <div className={cn('lemma-index-row flex items-center gap-3', className)} data-skeleton="true">
            <Skeleton shape="circle" className="h-5 w-5 shrink-0" />
            <Skeleton className={cn('h-3', widthClassName)} />
            {trailing ? <Skeleton className="ml-auto h-3 w-12 shrink-0" /> : null}
        </div>
    );
}

/**
 * Mirrors `lemma-index-list`. Row widths vary because real titles do — a stack
 * of identical bars reads as a table, and the eye stops believing it.
 */
const LIST_ROW_WIDTHS = ['w-3/5', 'w-5/12', 'w-2/3', 'w-1/2', 'w-5/12', 'w-7/12'];

export function ListSkeleton({
    rows = 4,
    trailing = true,
    className,
}: {
    rows?: number;
    trailing?: boolean;
    className?: string;
}) {
    return (
        <div className={cn('lemma-index-list', className)}>
            {Array.from({ length: rows }).map((_, index) => (
                <ListRowSkeleton
                    key={index}
                    widthClassName={LIST_ROW_WIDTHS[index % LIST_ROW_WIDTHS.length]}
                    trailing={trailing}
                />
            ))}
        </div>
    );
}

/**
 * Record rows for a real `<tbody>`.
 *
 * Rendered *inside* the settled table — same columns, same row padding — so the
 * grid keeps its height while records land. The alternative this replaces was a
 * single "Loading records…" cell, which collapsed a page-sized body to one line
 * and then expanded it again.
 */
export function TableRowsSkeleton({
    rows = 8,
    columns,
    cellClassName,
}: {
    rows?: number;
    columns: number;
    cellClassName?: string;
}) {
    return (
        <>
            {Array.from({ length: rows }).map((_, rowIndex) => (
                <tr key={rowIndex} aria-hidden="true">
                    {Array.from({ length: columns }).map((_, columnIndex) => (
                        <td key={columnIndex} className={cn('px-3 py-2', cellClassName)}>
                            <Skeleton className={cn('h-3', columnIndex === 0 ? 'w-5/12' : 'w-2/3')} />
                        </td>
                    ))}
                </tr>
            ))}
        </>
    );
}

/**
 * A turn of conversation — one short line for the human, a longer block for the
 * reply. Bottom-anchored transcripts settle instead of jumping when the real
 * first message is roughly this tall.
 */
export function MessageSkeleton({ role = 'assistant' }: { role?: 'user' | 'assistant' }) {
    if (role === 'user') {
        return (
            <div className="flex w-full justify-end" aria-hidden="true">
                <Skeleton shape="block" className="h-8 w-2/5 rounded-2xl" />
            </div>
        );
    }

    return (
        <div className="flex w-full flex-col gap-2" aria-hidden="true">
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-11/12" />
            <Skeleton className="h-3 w-2/3" />
        </div>
    );
}

/** The transcript's first-load fill: a couple of turns, in reading order. */
export function TranscriptSkeleton({ turns = 2, className }: { turns?: number; className?: string }) {
    return (
        <div className={cn('flex w-full flex-col gap-5', className)}>
            {Array.from({ length: turns }).map((_, index) => (
                <div key={index} className="flex w-full flex-col gap-5">
                    <MessageSkeleton role="user" />
                    <MessageSkeleton role="assistant" />
                </div>
            ))}
        </div>
    );
}

/** A labelled form/detail row — label above, value below. */
export function FieldRowsSkeleton({ rows = 4, className }: { rows?: number; className?: string }) {
    return (
        <div className={cn('space-y-4', className)}>
            {Array.from({ length: rows }).map((_, index) => (
                <div key={index} className="space-y-2">
                    <Skeleton className="h-3 w-24" />
                    <Skeleton shape="block" className="h-8 w-full" />
                </div>
            ))}
        </div>
    );
}
