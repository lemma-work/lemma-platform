'use client';

import { AlertTriangle } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import { describeConnection } from '@/lib/utils/surfaces';
import type { AssistantSurface } from '@/lib/types';

/**
 * Which account this surface runs on, and who connected it.
 *
 * Accounts are personal, so for everyone but their owner the surface used to say
 * only that it was live — a bot that went dark had no owner anyone could name.
 * This states the fact plainly, and where the answer is "the person who can fix
 * this isn't here any more", it offers the repair that is actually available to
 * the reader: point the surface at their own account.
 *
 * The credential is never part of this. Only its owner ever holds that.
 */
export function SurfaceConnectionRow({
    surface,
    onRebind,
}: {
    surface: AssistantSurface;
    /** Starts the connect journey again, binding the result to this surface. */
    onRebind: () => void;
}) {
    const connection = describeConnection(surface);
    if (!connection) return null;

    return (
        <div className="surface-panel-muted grid gap-2 p-3">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                {connection.label ? (
                    <span className="truncate font-mono text-sm text-[var(--text-primary)]">
                        {connection.label}
                    </span>
                ) : null}
                <span className="text-xs text-[var(--text-secondary)]">
                    {connection.attribution}
                </span>
            </div>

            {connection.problem ? (
                <p className="flex items-start gap-2 text-xs leading-5 text-[var(--text-secondary)]">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--state-warning)]" />
                    <span className="min-w-0">{connection.problem}</span>
                </p>
            ) : null}

            {connection.canRebind ? (
                <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="w-fit"
                    onClick={onRebind}
                >
                    Use my account instead
                </Button>
            ) : null}
        </div>
    );
}
