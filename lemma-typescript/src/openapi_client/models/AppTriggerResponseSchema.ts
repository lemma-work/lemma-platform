/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectorKind } from './ConnectorKind.js';
/**
 * Schema for trigger response.
 */
export type AppTriggerResponseSchema = {
    config_schema: (Record<string, any> | null);
    connector_id: (string | null);
    created_at: string;
    description: (string | null);
    id: string;
    kind: ConnectorKind;
    payload_example: (Record<string, any> | null);
    payload_schema: (Record<string, any> | null);
    updated_at: string;
};
