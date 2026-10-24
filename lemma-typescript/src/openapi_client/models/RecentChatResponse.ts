/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConversationOrigin } from './ConversationOrigin.js';
export type RecentChatResponse = {
    agent_id?: (string | null);
    channel_name?: (string | null);
    conversation_id: string;
    last_activity_at: string;
    /**
     * `chat` or `channel` -- or, for a conversation waiting on its owner, whatever started it.
     */
    origin: ConversationOrigin;
    /**
     * The platform a channel's conversation came in on.
     */
    platform?: (string | null);
    /**
     * RUNNING while an agent works in it.
     */
    status?: (string | null);
    title?: (string | null);
    waiting_on_you: boolean;
};
