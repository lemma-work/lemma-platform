'use client';

import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { formatAgentName } from '@/lib/utils/agents';
import type { Agent } from '@/lib/types';

export const POD_DEFAULT_AGENT_VALUE = '__pod_default_agent__';

/**
 * Who should answer.
 *
 * Lifted out of `ConversationComposerContext` so pod home can render it too —
 * home could pick a *model* but not an agent, so starting a conversation with
 * a particular agent meant going to /new and retyping the sentence there.
 */
export function ConversationAgentPicker({
    agents,
    selectedAgentName,
    onAgentChange,
    disabled = false,
    label,
}: {
    agents: Agent[];
    selectedAgentName: string | null;
    onAgentChange: (agentName: string | null) => void;
    disabled?: boolean;
    label?: string;
}) {
    const value = selectedAgentName || POD_DEFAULT_AGENT_VALUE;
    const shown = label ?? (selectedAgentName ? formatAgentName(selectedAgentName) : 'Pod default');

    return (
        <Select
            value={value}
            onValueChange={(next) => onAgentChange(next === POD_DEFAULT_AGENT_VALUE ? null : next)}
            disabled={disabled}
        >
            <SelectTrigger
                className="h-8 w-auto max-w-24 rounded-lg border border-[var(--row-border)] bg-[var(--field-bg)] px-2 py-0 text-xs font-normal shadow-none sm:max-w-44"
                aria-label="Conversation agent"
                title={`Agent: ${shown}`}
            >
                <SelectValue>{shown}</SelectValue>
            </SelectTrigger>
            <SelectContent align="start">
                <SelectItem value={POD_DEFAULT_AGENT_VALUE}>Pod default</SelectItem>
                {agents.map((agent) => (
                    <SelectItem key={agent.id || agent.name} value={agent.name}>
                        {formatAgentName(agent.name)}
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
}
