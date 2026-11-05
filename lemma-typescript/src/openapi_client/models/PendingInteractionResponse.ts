/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConversationStatus } from './ConversationStatus.js';
import type { PendingInteractionKind } from './PendingInteractionKind.js';
/**
 * One pause waiting on the caller, and the conversation it is in.
 */
export type PendingInteractionResponse = {
    /**
     * Absent for the pod's own assistant on older conversations.
     */
    agent_id?: (string | null);
    agent_name?: (string | null);
    conversation_id: string;
    conversation_status?: (ConversationStatus | null);
    conversation_title?: (string | null);
    created_at: string;
    kind: PendingInteractionKind;
    /**
     * The question asked, or the approval's title, on one line.
     */
    summary?: (string | null);
    /**
     * The paused call, which is also the approval id the decision endpoint takes.
     */
    tool_call_id: string;
};
