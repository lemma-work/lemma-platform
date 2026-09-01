'use client';

import { useMemo, useState } from 'react';
import { Users } from '@/components/ui/icons';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from '@/components/ui/popover';
import {
    useAddConversationParticipant,
    useConversationParticipants,
    useRemoveConversationParticipant,
} from '@/lib/hooks/use-assistants';
import { usePodMembers } from '@/lib/hooks/use-pod-members';
import { useAgents } from '@/lib/hooks/use-agents';
import { formatAgentName } from '@/lib/utils/agents';

/**
 * Who is in a conversation, and how somebody else gets in.
 *
 * Adding a person is a grant, not an invitation: from then on every answer in
 * the conversation is said to them, including answers produced from data they
 * could not have reached themselves. Their own working stays theirs — the
 * transcript withholds it — and so does everyone else's. The copy says so,
 * because a roster that reads like a share sheet invites the wrong assumption.
 */
export function ConversationParticipants({
    podId,
    conversationId,
}: {
    podId: string | null | undefined;
    conversationId: string | null | undefined;
}) {
    const [open, setOpen] = useState(false);
    const { data: participants } = useConversationParticipants(podId, conversationId);
    const { data: members } = usePodMembers(podId || '');
    const { data: agents } = useAgents(podId || undefined);
    const addParticipant = useAddConversationParticipant(podId);
    const removeParticipant = useRemoveConversationParticipant(podId);

    const people = useMemo(
        () => (participants ?? []).filter((participant) => !!participant.user_id),
        [participants],
    );
    const presentAgents = useMemo(
        () => (participants ?? []).filter((participant) => !!participant.agent_id),
        [participants],
    );
    const presentUserIds = useMemo(
        () => new Set(people.map((participant) => participant.user_id)),
        [people],
    );
    const presentAgentIds = useMemo(
        () => new Set(presentAgents.map((participant) => participant.agent_id)),
        [presentAgents],
    );

    // Only what can actually be added: a pod member already here is not a
    // choice, and offering them means the first thing the control does is fail.
    const addablePeople = (members?.items ?? []).filter(
        (member) => !presentUserIds.has(member.user_id),
    );
    const addableAgents = (agents?.items ?? []).filter(
        (agent) => !presentAgentIds.has(agent.id),
    );

    if (!podId || !conversationId) return null;

    const add = async (subject: { user_id?: string; agent_name?: string }) => {
        try {
            await addParticipant.mutateAsync({ conversationId, ...subject });
        } catch {
            toast.error('Could not add them to this conversation');
        }
    };

    const remove = async (subject: { user_id?: string; agent_id?: string }) => {
        try {
            await removeParticipant.mutateAsync({ conversationId, ...subject });
        } catch {
            // The owner is refused by the server rather than hidden here: the
            // rule belongs in one place, and a button that silently does
            // nothing is worse than one that says why.
            toast.error('The person who opened this conversation cannot be removed');
        }
    };

    const count = (participants ?? []).length;

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button variant="quiet" size="xs" title="Who is in this conversation">
                    <Users className="h-3.5 w-3.5" />
                    {count > 1 ? count : null}
                </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-72 p-0">
                <div className="border-b border-[var(--border-subtle)] px-3 py-2">
                    <div className="text-sm text-[var(--text-primary)]">In this conversation</div>
                    <p className="mt-0.5 text-xs text-[var(--text-secondary)]">
                        Everyone here sees the answers. Each person&rsquo;s own tool
                        calls and thinking stay private to them.
                    </p>
                </div>
                <ul className="max-h-56 overflow-y-auto py-1">
                    {(participants ?? []).map((participant) => (
                        <li
                            key={participant.user_id ?? participant.agent_id}
                            className="flex items-center justify-between gap-2 px-3 py-1.5"
                        >
                            <span className="min-w-0 truncate text-sm text-[var(--text-primary)]">
                                {participant.display_name
                                    ?? (participant.agent_id ? 'Agent' : 'Someone')}
                            </span>
                            <span className="flex shrink-0 items-center gap-2">
                                {participant.role === 'OWNER' ? (
                                    <span className="text-xs text-[var(--text-tertiary)]">opened it</span>
                                ) : (
                                    <Button
                                        variant="quiet"
                                        size="xs"
                                        onClick={() => void remove({
                                            user_id: participant.user_id ?? undefined,
                                            agent_id: participant.agent_id ?? undefined,
                                        })}
                                    >
                                        Remove
                                    </Button>
                                )}
                            </span>
                        </li>
                    ))}
                </ul>
                {addablePeople.length > 0 || addableAgents.length > 0 ? (
                    <div className="border-t border-[var(--border-subtle)] py-1">
                        {addablePeople.map((member) => (
                            <Button
                                key={member.user_id}
                                variant="quiet"
                                size="xs"
                                className="w-full justify-start"
                                onClick={() => void add({ user_id: member.user_id })}
                            >
                                Add {member.user_name || member.user_email}
                            </Button>
                        ))}
                        {addableAgents.map((agent) => (
                            <Button
                                key={agent.id}
                                variant="quiet"
                                size="xs"
                                className="w-full justify-start"
                                onClick={() => void add({ agent_name: agent.name })}
                            >
                                Add {formatAgentName(agent.name)}
                            </Button>
                        ))}
                    </div>
                ) : null}
            </PopoverContent>
        </Popover>
    );
}
