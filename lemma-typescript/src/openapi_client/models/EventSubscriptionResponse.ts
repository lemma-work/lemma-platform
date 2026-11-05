/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Something a connected app asked to be told about.
 */
export type EventSubscriptionResponse = {
    arguments: Record<string, any>;
    id: string;
    last_delivery_at: (string | null);
    last_error: (string | null);
    name: string;
    /**
     * When delivery gave up on a callback that kept failing. The app's next refresh resumes it.
     */
    paused_at?: (string | null);
    refresh_before: string;
    /**
     * When the person pressed Stop. The app's refresh is refused until it is resumed.
     */
    stopped_at?: (string | null);
};
