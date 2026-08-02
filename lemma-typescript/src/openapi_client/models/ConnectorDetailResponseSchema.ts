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
    operations?: Record<string, OperationSummary>;
    title?: (string | null);
    updated_at: string;
};
