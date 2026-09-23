'use client';

import { Button } from '@/components/ui/button';
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
                    className="flex items-start gap-2 rounded-md border border-[color:var(--border)] bg-[var(--surface-2)] px-2.5 py-2 text-xs"
                >
                    <span className="mt-0.5 shrink-0 font-medium text-[var(--text-secondary)]">Queued</span>
                    <span className="min-w-0 flex-1 break-words text-[var(--text-secondary)]">{item.content}</span>
                    {onDiscard ? (
                        <Button
                            variant="quiet"
                            size="xs"
                            className="shrink-0"
                            // Named by its message, not just "Remove": with two
                            // queued, a screen reader otherwise announces two
                            // identical buttons and neither says what it drops.
                            aria-label={`Remove queued message: ${item.content}`}
                            onClick={() => onDiscard(item.id)}
                        >
                            Remove
                        </Button>
                    ) : null}
                </div>
            ))}
            <div className="flex items-center justify-between gap-2 px-0.5">
                <span className="text-xs text-[var(--text-secondary)]">
                    {items.length === 1
                        ? 'Will be sent when this turn finishes.'
                        : `${items.length} messages will be sent when this turn finishes.`}
                </span>
                {onSendNow ? (
                    <Button
                        variant="link"
                        size="xs"
                        className="shrink-0"
                        onClick={onSendNow}
                        // Said on the control, not only in a tooltip: the cost is
                        // the agent's unfinished work, and it is not recoverable.
                        title="Stop the current turn and send this now"
                    >
                        Send now
                    </Button>
                ) : null}
            </div>
        </div>
    );
}
