'use client';

import { useRouter } from 'next/navigation';
import { MessageSquarePlus } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import { requestConversationStageNavigation } from '@/lib/assistant/conversation-presentation';
import { cn } from '@/lib/utils';

// Opens the pod's new-conversation composer, optionally scoped to a specific
// agent via `?agent=` (the pod layout reads it and scopes the assistant). No
// server round-trip here — the conversation is created when the first message is
// sent, reusing the pod's existing new-conversation flow.
export function StartConversationButton({
    podId,
    agentName,
    label = 'Start conversation',
    variant = 'primary',
    size = 'sm',
    className,
}: {
    podId: string;
    /** A named agent, or null/undefined for the pod default assistant. */
    agentName?: string | null;
    label?: string;
    variant?: 'primary' | 'secondary' | 'outline' | 'ghost';
    size?: 'sm' | 'default' | 'lg';
    className?: string;
}) {
    const router = useRouter();

    const start = () => {
        const query = agentName ? `?agent=${encodeURIComponent(agentName)}` : '';
        const href = `/pod/${podId}/conversations/new${query}`;
        if (!requestConversationStageNavigation(href)) router.push(href);
    };

    return (
        <Button
            type="button"
            variant={variant}
            size={size}
            className={cn('gap-2', className)}
            onClick={start}
        >
            <MessageSquarePlus className="h-4 w-4" />
            {label}
        </Button>
    );
}
