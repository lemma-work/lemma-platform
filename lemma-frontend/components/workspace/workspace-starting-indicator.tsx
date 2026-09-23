'use client';

import { isComingUp, useWorkspaceStatus, type WorkspaceStatus } from '@/lib/hooks/use-workspace-status';

const TITLES = {
    downloading: 'Downloading your workspace',
    starting: 'Starting your computer',
} as const;

/**
 * A small note, bottom right, while the person's computer is coming up.
 *
 * Without it the file explorer and the browser pane were the only signs, and
 * both look broken while a computer is merely starting -- which after an update
 * means downloading a new workspace image for a few minutes.
 */
/** How far a download has got, when the guest can measure it. */
export const downloadFraction = (status: WorkspaceStatus): number | null => {
    const done = status.done_mb;
    const total = status.total_mb;
    if (status.state !== 'downloading' || done == null || !total) return null;
    return Math.min(1, Math.max(0, done / total));
};

export function WorkspaceStartingIndicator() {
    const { data } = useWorkspaceStatus();
    if (!data || !isComingUp(data)) return null;
    const title = TITLES[data.state as keyof typeof TITLES];
    const measured = downloadFraction(data);
    return (
        <div
            role="status"
            aria-live="polite"
            className="surface-panel pointer-events-none fixed bottom-4 right-4 z-50 flex max-w-xs items-start gap-3 px-4 py-3 shadow-[var(--shadow-lg)]"
        >
            <span
                aria-hidden="true"
                className="lemma-live-pulse mt-1.5 h-2 w-2 shrink-0 rounded-full bg-[var(--action-primary)]"
            />
            <div className="min-w-0">
                <p className="text-sm font-medium text-[var(--text-primary)]">
                    {title}
                    {measured !== null ? (
                        <span className="font-normal text-[var(--text-secondary)]">
                            {' '}
                            · {data.done_mb} of {data.total_mb} MB
                        </span>
                    ) : null}
                </p>
                {data.detail ? (
                    <p className="mt-0.5 text-xs text-[var(--text-secondary)]">{data.detail}</p>
                ) : null}
                <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-[var(--surface-2)]">
                    {measured !== null ? (
                        <div
                            className="h-full rounded-full bg-[var(--action-primary)] transition-[width] duration-700"
                            /* eslint-disable-next-line no-restricted-syntax -- Runtime progress scale is data-driven geometry. */
                            style={{ width: `${Math.round(measured * 100)}%` }}
                        />
                    ) : (
                        <div className="lemma-live-pulse h-full w-1/3 rounded-full bg-[var(--action-primary)]" />
                    )}
                </div>
            </div>
        </div>
    );
}
