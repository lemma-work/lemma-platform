'use client';

import { cn } from '@/lib/utils';

export type QueuedSteerItem = { id: string; content: string; queuedAt: string };

/**
 * Messages waiting for a turn that cannot be told anything mid-flight.
 *
 * ACP has no steering primitive: an Agent Host turn is a single
 * `session/prompt` that returns when it ends, so a message typed at one cannot
 * reach the agent until then. An in-process run is different — it is handed the
 * message on its next step — and the composer used to treat both the same,
 * putting the message in the transcript as though it had been delivered.
 *
 * So this says what is true: queued, with the choice of waiting for the turn or
 * cutting it short. Waiting is the default because interrupting throws away
 * whatever the agent has in flight, which is not a cost to pay by accident.
 */
export function AssistantQueuedSteers({
    items,
    onSendNow,
    onDiscard,
    className,
}: {
    items: QueuedSteerItem[];
    onSendNow?: () => void;
    onDiscard?: (id: string) => void;
    className?: string;
}) {
    if (items.length === 0) return null;
    return (
        <div
            className={cn('flex flex-col gap-1.5 px-1 pb-2', className)}
            // Announced, because the message the person just typed did not go
            // where they expected and the reason is only written here.
            role="status"
            aria-live="polite"
        >
            {items.map((item) => (
                <div
                    key={item.id}
                    className="flex items-start gap-2 rounded-md border border-[var(--border)] bg-[var(--surface-muted)] px-2.5 py-2 text-xs"
                >
                    <span className="mt-0.5 shrink-0 font-medium text-[var(--text-muted)]">Queued</span>
                    <span className="min-w-0 flex-1 break-words text-[var(--text-secondary)]">{item.content}</span>
                    {onDiscard ? (
                        <button
                            type="button"
                            className="shrink-0 rounded px-1.5 py-0.5 text-[var(--text-muted)] hover:bg-[var(--surface-hover)]"
                            onClick={() => onDiscard(item.id)}
                        >
                            Remove
                        </button>
                    ) : null}
                </div>
            ))}
            <div className="flex items-center justify-between gap-2 px-0.5">
                <span className="text-[11px] text-[var(--text-muted)]">
                    {items.length === 1 ? 'Will be sent when this turn finishes.' : `${items.length} messages will be sent when this turn finishes.`}
                </span>
                {onSendNow ? (
                    <button
                        type="button"
                        className="shrink-0 rounded px-2 py-0.5 text-[11px] font-medium text-[var(--text-secondary)] underline-offset-2 hover:underline"
                        onClick={onSendNow}
                        // Said on the control, not only in a tooltip: the cost is
                        // the agent's unfinished work, and it is not recoverable.
                        title="Stop the current turn and send this now"
                    >
                        Send now
                    </button>
                ) : null}
            </div>
        </div>
    );
}
