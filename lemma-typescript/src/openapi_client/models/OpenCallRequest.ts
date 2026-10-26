/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OpenCallRequest = {
    /**
     * The pod agent to call in a new conversation; empty calls the pod's default agent. A call in an existing chat is with that chat's agent.
     */
    agent_name?: (string | null);
    /**
     * Hold the call in this chat -- your own, in this pod -- so the call, the agent's work and the transcript are that one chat; a call already held there is picked up again. Empty starts a new conversation.
     */
    conversation_id?: (string | null);
    /**
     * What the agent is called in a new call's title.
     */
    display_name?: string;
    /**
     * A new call conversation's title; an existing chat keeps its own.
     */
    title?: (string | null);
};
