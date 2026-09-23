import { cn } from '@/lib/utils';

/**
 * The one skeleton atom.
 *
 * Everything that stands in for content while it loads is built from this —
 * there is no second way to draw a placeholder. `shape` picks the silhouette:
 * `text` for a line of prose, `block` for a control or tile, `circle` for an
 * avatar or monogram. Size comes from the caller, because a skeleton is only
 * honest if it is measured off the thing it replaces.
 *
 * It is `aria-hidden` by design. The *region* announces that it is loading
 * (see `AsyncRegion`); a screen reader has nothing to gain from each bar.
 */
export function Skeleton({
    shape = 'text',
    className,
}: {
    shape?: 'text' | 'block' | 'circle';
    className?: string;
}) {
    return (
        <span
            aria-hidden="true"
            className={cn(
                'lemma-skeleton block',
                shape === 'block' ? 'rounded-md' : 'rounded-full',
                className
            )}
        />
    );
}

/**
 * A run of text lines — the shape a paragraph leaves behind.
 *
 * The last line is short on purpose: prose does not fill its final line, and a
 * block of equal-length bars reads as a table, not a sentence.
 */
export function SkeletonText({
    lines = 3,
    className,
    lineClassName,
}: {
    lines?: number;
    className?: string;
    lineClassName?: string;
}) {
    return (
        <span className={cn('block space-y-2', className)}>
            {Array.from({ length: lines }).map((_, index) => (
                <Skeleton
                    key={index}
                    className={cn('h-3', index === lines - 1 ? 'w-3/5' : 'w-full', lineClassName)}
                />
            ))}
        </span>
    );
}
