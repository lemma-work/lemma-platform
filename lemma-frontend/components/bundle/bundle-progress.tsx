'use client';

import { cn } from '@/lib/utils';

interface BundleProgressBarProps {
    done: number;
    total: number;
    label?: string;
    className?: string;
}

/**
 * A determinate progress bar driven by a job's done/total counts. Falls back to
 * an indeterminate pulse before the total is known.
 */
export function BundleProgressBar({ done, total, label, className }: BundleProgressBarProps) {
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : null;

    return (
        <div className={cn('space-y-1.5', className)}>
            {label ? (
                <div className="flex items-center justify-between text-xs text-[var(--text-tertiary)]">
                    <span>{label}</span>
                    {pct !== null ? (
                        <span className="tabular-nums">
                            {done}/{total}
                        </span>
                    ) : null}
                </div>
            ) : null}
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--surface-2)]">
                <div
                    className={cn(
                        'h-full rounded-full bg-[var(--action-primary)] transition-[width] duration-300 ease-out',
                        pct === null && 'w-1/3 lemma-live-pulse',
                    )}
                    /* eslint-disable-next-line no-restricted-syntax -- Runtime progress scale is data-driven geometry. */
                    style={pct !== null ? { width: `${pct}%` } : undefined}
                />
            </div>
        </div>
    );
}
