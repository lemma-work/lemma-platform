/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OpenCallRequest = {
    /**
     * The pod agent to call; empty calls the pod's default agent.
     */
    agent_name?: (string | null);
    /**
     * What the agent is called on the call.
     */
    display_name?: string;
    /**
     * The call conversation's title.
     */
    title?: (string | null);
};
