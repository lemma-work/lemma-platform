'use client';

import type {
    AgentRuntimeConfig,
    AgentRuntimeProfileListResponse,
} from 'lemma-sdk';

import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { resolveRuntimeModelName, shortModelName } from '@/components/agents/agent-runtime-helpers';
import { RuntimeModelPicker } from '@/components/lemma/assistant/model-picker';
import { ProjectBranchChip } from '@/components/lemma/assistant/project-branch';
import { ProjectPicker } from '@/components/lemma/assistant/project-picker';
import type { ProjectSelection } from '@/lib/assistant/project-selection';
import { useGithubProjects } from '@/lib/hooks/use-github-projects';
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
    isNewConversation,
    canWrite,
    onAgentChange,
    onRuntimeChange,
    manageModelsHref,
    podId,
    boundProject = null,
}: {
    agents: Agent[];
    selectedAgentName: string | null;
    agentDisplayLabel?: string;
    selectedRuntime: AgentRuntimeConfig | null;
    defaultRuntime: AgentRuntimeConfig | null | undefined;
    runtimeCatalog?: AgentRuntimeProfileListResponse;
    isNewConversation: boolean;
    canWrite: boolean;
    onAgentChange: (agentName: string | null) => void;
    onRuntimeChange: (runtime: AgentRuntimeConfig | null) => void;
    manageModelsHref?: string;
    /** Where "Connect GitHub" goes: connectors live on the pod, not the org. */
    podId: string;
    /** The project an existing conversation is already working in. */
    boundProject?: ProjectSelection | null;
}) {
    const { pendingProject, setPendingProject } = useAIAssistant();
    // Only the picker needs the repo list, and only before a conversation
    // exists — an open conversation's directory is already decided.
    const githubProjects = useGithubProjects({ enabled: isNewConversation });
    const agentLabel = agentDisplayLabel
        ?? (selectedAgentName ? formatAgentName(selectedAgentName) : 'Pod default');
    const agentValue = selectedAgentName || POD_DEFAULT_AGENT_VALUE;
    // Neither runtime is required to carry a model — an inherited default names
    // only its profile — so resolve both through the catalog the run will use.
    // "Default" survives only until the catalog loads, or where nothing is set
    // up to run at all; naming the model is the whole point of this row.
    const defaultModelName = resolveRuntimeModelName(defaultRuntime, runtimeCatalog);
    const resolvedModelName = resolveRuntimeModelName(selectedRuntime, runtimeCatalog)
        ?? defaultModelName;
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
                {boundProject ? (
                    <>
                        <ProjectPicker
                            value={boundProject}
                            onChange={() => undefined}
                            projects={[]}
                            isConnected
                            isLoadingProjects={false}
                            readOnly
                            connectHref="#"
                            className="h-auto px-0"
                        />
                        {/* The branch is settled, but what happened to it is not:
                            a pull request can open, fill up and merge while this
                            conversation is still going. */}
                        <ProjectBranchChip project={boundProject} readOnly />
                    </>
                ) : null}
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
                defaultRuntime={defaultRuntime}
                value={selectedRuntime}
                onChange={onRuntimeChange}
                disabled={!canWrite}
                compact
                scopeHint="Just for this chat"
                manageHref={manageModelsHref}
                autoTriggerLabel={defaultModelName ? shortModelName(defaultModelName) : 'Default'}
                className="min-w-0 [&>button]:max-w-28 sm:[&>button]:max-w-52"
                triggerClassName="text-xs font-normal"
                triggerLabelClassName="text-xs font-normal"
                title="Choose a conversation model"
                description="Pick the model before this conversation starts."
            />

            {canWrite ? (
                <>
                    <span aria-hidden="true" className="shrink-0 text-xs text-[var(--text-soft)]">·</span>
                    <ProjectPicker
                        value={pendingProject}
                        onChange={setPendingProject}
                        projects={githubProjects.projects}
                        isConnected={githubProjects.isConnected}
                        isLoadingProjects={githubProjects.isLoadingProjects}
                        error={githubProjects.error}
                        accountId={githubProjects.accountId}
                        connectHref={`/pod/${encodeURIComponent(podId)}/connectors`}
                    />
                    {pendingProject ? (
                        <ProjectBranchChip
                            project={pendingProject}
                            onChange={(ref) => setPendingProject({ ...pendingProject, ref })}
                        />
                    ) : null}
                </>
            ) : null}
        </div>
    );
}
