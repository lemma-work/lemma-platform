'use client';

import type {
    AgentRuntimeConfig,
    AgentRuntimeProfileListResponse,
} from 'lemma-sdk';

import { useEffect, useRef } from 'react';

import { useAIAssistant } from '@/components/ai/ai-assistant-context';
import { resolveRuntimeModelName, shortModelName } from '@/components/agents/agent-runtime-helpers';
import { RuntimeModelPicker } from '@/components/lemma/assistant/model-picker';
import { ProjectBranchChip } from '@/components/lemma/assistant/project-branch';
import { ProjectPicker } from '@/components/lemma/assistant/project-picker';
import { adoptConversationFolder, useConversationFolder } from '@/lib/hooks/use-conversation-folder';
import type { ProjectSelection } from '@/lib/assistant/project-selection';
import { useGithubProjects } from '@/lib/hooks/use-github-projects';
import { formatAgentName } from '@/lib/utils/agents';
import type { Agent } from '@/lib/types';
import { ConversationAgentPicker } from './conversation-agent-picker';


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
    conversationId = null,
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
    /** The open conversation, or null while one is being composed. */
    conversationId?: string | null;
}) {
    const { pendingProject, setPendingProject } = useAIAssistant();
    // Only the picker needs the repo list, and only before a conversation
    // exists — an open conversation's directory is already decided.
    const githubProjects = useGithubProjects({ enabled: isNewConversation });
    // A folder on this computer, which only a local install can offer. Asked
    // for the open conversation, or for the one being composed — the shell
    // holds that choice until there is an id to give it.
    const localFolder = useConversationFolder(isNewConversation ? null : conversationId);

    // A folder chosen in the composer is parked in the shell until the
    // conversation exists. Adopted only on the transition out of composing:
    // adopting whenever an id appears would hand the parked choice to whatever
    // conversation the person happened to open next.
    const wasComposingRef = useRef(isNewConversation);
    useEffect(() => {
        const wasComposing = wasComposingRef.current;
        wasComposingRef.current = isNewConversation;
        if (!wasComposing || isNewConversation || !conversationId) return;
        void adoptConversationFolder(conversationId, localFolder.pendingId);
    }, [conversationId, isNewConversation, localFolder.pendingId]);
    const agentLabel = agentDisplayLabel
        ?? (selectedAgentName ? formatAgentName(selectedAgentName) : 'Pod default');
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
                {boundProject || localFolder.folder ? (
                    <>
                        <ProjectPicker
                            value={boundProject}
                            onChange={() => undefined}
                            projects={[]}
                            isConnected
                            isLoadingProjects={false}
                            readOnly
                            connectHref="#"
                            localFolder={localFolder.folder}
                            className="h-auto px-0"
                        />
                        {/* The branch is settled, but what happened to it is not:
                            a pull request can open, fill up and merge while this
                            conversation is still going. */}
                        {boundProject ? <ProjectBranchChip project={boundProject} readOnly /> : null}
                    </>
                ) : null}
            </div>
        );
    }

    return (
        <div className="flex min-w-0 items-center gap-1">
            <ConversationAgentPicker
                agents={agents}
                selectedAgentName={selectedAgentName}
                onAgentChange={onAgentChange}
                disabled={!canWrite}
                label={agentLabel}
            />

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
                ariaLabel="Conversation model"
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
                        localFolder={localFolder.folder}
                        onPickLocalFolder={localFolder.available ? localFolder.bind : undefined}
                        onClearLocalFolder={localFolder.available ? localFolder.unbind : undefined}
                        // A GitHub connection needs an app registration, secrets
                        // and a reachable webhook. A local install has none of
                        // those, so offering "Connect GitHub" there is offering
                        // something that cannot be finished — a folder on this
                        // computer is the answer instead.
                        canConnectGithub={!localFolder.available}
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
