/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRuntimeConfig } from './AgentRuntimeConfig.js';
import type { ConversationType } from './ConversationType.js';
export type CreateConversationRequest = {
    agent_name?: (string | null);
    agent_runtime?: (AgentRuntimeConfig | null);
    instructions?: (string | null);
    metadata?: (Record<string, any> | null);
    parent_id?: (string | null);
    /**
     * A GitHub repository to work in, as 'owner/name': one the organization's GitHub installation covers for you, as listed by the conversation repositories endpoint. The conversation's workspace_cwd is then a git worktree of it on a branch of its own (lemma/<slug>), made before the agent's first command from one clone of the repository, which is fetched first. Other conversations in the repository get worktrees of their own.
     */
    repository?: (string | null);
    title?: (string | null);
    type?: ConversationType;
};
