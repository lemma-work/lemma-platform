/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectorKindResponseSchema } from './ConnectorKindResponseSchema.js';
/**
 * Schema for connector response.
 */
export type ConnectorResponseSchema = {
    created_at: string;
    description: (string | null);
    icon: (string | null);
    id: string;
    is_active: boolean;
    kinds?: Array<ConnectorKindResponseSchema>;
    title?: (string | null);
    updated_at: string;
};
