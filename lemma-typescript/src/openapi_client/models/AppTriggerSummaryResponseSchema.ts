/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectorKind } from './ConnectorKind.js';
/**
 * Lean trigger shape for list responses.
 *
 * Omits the heavy `config_schema` / `payload_schema` / `payload_example` JSON
 * blobs — fetch those from `connector.trigger.get`.
 */
export type AppTriggerSummaryResponseSchema = {
    connector_id: (string | null);
    created_at: string;
    description: (string | null);
    id: string;
    kind: ConnectorKind;
    updated_at: string;
};
