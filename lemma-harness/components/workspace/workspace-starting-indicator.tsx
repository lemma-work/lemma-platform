'use client';

import { isComingUp, useWorkspaceStatus } from '@/lib/hooks/use-workspace-status';

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
export function WorkspaceStartingIndicator() {
    const { data } = useWorkspaceStatus();
    if (!data || !isComingUp(data)) return null;
    const title = TITLES[data.state as keyof typeof TITLES];
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
                <p className="text-sm font-medium text-[var(--text-primary)]">{title}</p>
                {data.detail ? (
                    <p className="mt-0.5 text-xs text-[var(--text-secondary)]">{data.detail}</p>
                ) : null}
                <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-[var(--surface-2)]">
                    <div className="lemma-live-pulse h-full w-1/3 rounded-full bg-[var(--action-primary)]" />
                </div>
            </div>
        </div>
    );
}
