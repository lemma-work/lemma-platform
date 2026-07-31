/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ComposioProviderCapabilityResponseSchema } from './ComposioProviderCapabilityResponseSchema.js';
import type { LemmaProviderCapabilityResponseSchema } from './LemmaProviderCapabilityResponseSchema.js';
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
    operations?: Record<string, OperationSummary>;
    provider_capabilities?: Array<(LemmaProviderCapabilityResponseSchema | ComposioProviderCapabilityResponseSchema)>;
    title?: (string | null);
    updated_at: string;
};
