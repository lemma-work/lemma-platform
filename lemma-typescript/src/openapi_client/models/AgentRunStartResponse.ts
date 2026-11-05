/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type AgentRunStartResponse = {
    agent_run_id: string;
    conversation_id: string;
    /**
     * True when this send was already received -- the same client_message_id, or the same text from the same person a moment ago. Nothing new was stored; agent_run_id is the run already answering it.
     */
    deduplicated?: boolean;
    started_new_run: boolean;
};
