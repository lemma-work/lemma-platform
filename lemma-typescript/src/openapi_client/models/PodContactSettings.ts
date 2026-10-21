/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * How the pod treats the people outside it who write to it.
 */
export type PodContactSettings = {
    /**
     * Said once to a contact who writes while their channel's agents are off -- for example "Thanks, someone from {pod} will reply" -- until a member writes to them or their channel is closed. ``{pod}`` is the pod's name. Omitted means nothing is said.
     */
    auto_acknowledge?: (string | null);
};
