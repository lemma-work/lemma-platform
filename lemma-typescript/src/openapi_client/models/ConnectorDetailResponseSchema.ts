/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectorKindResponseSchema } from './ConnectorKindResponseSchema.js';
import type { OperationSummary } from './OperationSummary.js';
/**
 * Schema for connector details including operation catalog.
 */
export type ConnectorDetailResponseSchema = {
    created_at: string;
    description: (string | null);
    icon: (string | null);
    id: string;
    is_active: boolean;
    kinds?: Array<ConnectorKindResponseSchema>;
    /**
     * The redirect URI an OAuth app registered for this deployment must allow -- the callback every sign-in returns to. The same for every connector; published here because this is what a person reads while registering their own app, and a hand-built copy of it is how a wrong path reached users as redirect_uri_mismatch.
     */
    oauth_redirect_uri?: (string | null);
    operations?: Record<string, OperationSummary>;
    title?: (string | null);
    updated_at: string;
};
