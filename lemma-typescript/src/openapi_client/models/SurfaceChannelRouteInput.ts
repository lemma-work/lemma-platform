/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One channel this surface's agent may be spoken to in.
 *
 * An allow-list entry. A surface belongs to exactly one agent, so a channel
 * says *where*, never *who* — it used to name an agent, back when one bot
 * could serve several.
 */
export type SurfaceChannelRouteInput = {
    channel_id?: (string | null);
    channel_name?: (string | null);
};
