'use client';

import type {
    AgentHarnessListResponse,
    AgentRuntimeConfig,
    AgentRuntimeProfileListResponse,
} from 'lemma-sdk';

import { shortModelName } from '@/components/agents/agent-runtime-helpers';
import { RuntimeModelPicker } from '@/components/lemma/assistant/model-picker';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { formatAgentName } from '@/lib/utils/agents';
import type { Agent } from '@/lib/types';

const POD_DEFAULT_AGENT_VALUE = '__pod_default_agent__';

export function ConversationComposerContext({
    agents,
    selectedAgentName,
    agentDisplayLabel,
    selectedRuntime,
    defaultRuntime,
    runtimeCatalog,
    availableHarnesses,
    isNewConversation,
    canWrite,
    onAgentChange,
    onRuntimeChange,
    manageModelsHref,
}: {
    agents: Agent[];
    selectedAgentName: string | null;
    agentDisplayLabel?: string;
    selectedRuntime: AgentRuntimeConfig | null;
    defaultRuntime: AgentRuntimeConfig | null | undefined;
    runtimeCatalog?: AgentRuntimeProfileListResponse;
    availableHarnesses?: AgentHarnessListResponse;
    isNewConversation: boolean;
    canWrite: boolean;
    onAgentChange: (agentName: string | null) => void;
    onRuntimeChange: (runtime: AgentRuntimeConfig | null) => void;
    manageModelsHref?: string;
}) {
    const agentLabel = agentDisplayLabel
        ?? (selectedAgentName ? formatAgentName(selectedAgentName) : 'Pod default');
    const agentValue = selectedAgentName || POD_DEFAULT_AGENT_VALUE;
    const resolvedModelName = selectedRuntime?.model_name ?? defaultRuntime?.model_name ?? null;
    const modelLabel = resolvedModelName ? shortModelName(resolvedModelName) : 'Default';

    if (!isNewConversation) {
        return (
            <div className="flex h-8 min-w-0 items-center gap-1.5 px-2 text-xs font-normal text-[var(--text-secondary)]">
                <span className="max-w-28 truncate sm:max-w-48" title={`Agent: ${agentLabel}`}>
                    {agentLabel}
                </span>
                <span aria-hidden="true" className="shrink-0 text-[var(--text-soft)]">·</span>
                <span className="max-w-28 truncate sm:max-w-52" title={`Model: ${modelLabel}`}>
                    {modelLabel}
                </span>
            </div>
        );
    }

    return (
        <div className="flex min-w-0 items-center gap-1">
            <Select
                value={agentValue}
                onValueChange={(value) => onAgentChange(value === POD_DEFAULT_AGENT_VALUE ? null : value)}
                disabled={!canWrite}
            >
                <SelectTrigger
                    className="h-8 w-auto max-w-24 rounded-lg border border-[var(--row-border)] bg-[var(--field-bg)] px-2 py-0 text-xs font-normal shadow-none sm:max-w-44"
                    aria-label="Conversation agent"
                    title={`Agent: ${agentLabel}`}
                >
                    <SelectValue>{agentLabel}</SelectValue>
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

            <span aria-hidden="true" className="shrink-0 text-xs text-[var(--text-soft)]">·</span>

            <RuntimeModelPicker
                catalog={runtimeCatalog}
                availableHarnesses={availableHarnesses}
                defaultRuntime={defaultRuntime}
                value={selectedRuntime}
                onChange={onRuntimeChange}
                disabled={!canWrite}
                compact
                scopeHint="Just for this chat"
                manageHref={manageModelsHref}
                autoTriggerLabel={defaultRuntime?.model_name ? shortModelName(defaultRuntime.model_name) : 'Default'}
                className="min-w-0 [&>button]:max-w-28 sm:[&>button]:max-w-52"
                triggerClassName="text-xs font-normal"
                triggerLabelClassName="text-xs font-normal"
                title="Choose a conversation model"
                description="Pick the model before this conversation starts."
            />
        </div>
    );
}
