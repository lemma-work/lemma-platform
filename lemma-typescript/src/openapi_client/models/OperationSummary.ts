/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Compact operation metadata for discovery flows.
 */
export type OperationSummary = {
    /**
     * Install this operation belongs to (org-wide search only).
     */
    auth_config?: (string | null);
    /**
     * Connector this operation belongs to (org-wide search only).
     */
    connector_id?: (string | null);
    description?: (string | null);
    name: string;
    /**
     * Relative relevance for the discovery query, from 0 to 1.
     */
    relevance_score?: (number | null);
};
