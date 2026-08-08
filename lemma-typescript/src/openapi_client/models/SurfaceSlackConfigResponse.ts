/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Slack settings as read back. ``dm_agent_by_user`` maps a Slack user id to
 * the agent that person chose, or ``__pod_assistant__`` when they explicitly
 * chose the pod assistant. A user absent from the map has never chosen and
 * falls to the surface default.
 */
export type SurfaceSlackConfigResponse = {
    app_name?: (string | null);
    dm_agent_by_user?: Record<string, string>;
};
