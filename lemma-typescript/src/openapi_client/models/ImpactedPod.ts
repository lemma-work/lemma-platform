/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One pod the caller can open, and what in it stops.
 */
export type ImpactedPod = {
    event_subscription_count?: number;
    name: string;
    /**
     * Slugs of its connectors on it.
     */
    pod_connectors?: Array<string>;
    pod_id: string;
    schedule_count?: number;
    surface_count?: number;
};
