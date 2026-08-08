/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One channel's routing, in the same three states the domain models.
 *
 * ``use_pod_assistant`` is not a synonym for an absent ``agent_name`` — see
 * :class:`SurfaceChannelRoute`. Omitting it here is what silently turned an
 * explicit "the pod assistant answers here", picked from inside Slack, back
 * into "unconfigured" on the next save from the web UI.
 */
export type SurfaceChannelRouteInput = {
    agent_name?: (string | null);
    channel_id?: (string | null);
    channel_name?: (string | null);
    use_pod_assistant?: boolean;
};
