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
    refresh_before: string;
};
