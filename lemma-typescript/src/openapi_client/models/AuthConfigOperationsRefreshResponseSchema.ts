/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OperationDiscoveryStatus } from './OperationDiscoveryStatus.js';
/**
 * The result of the refresh endpoint, which is the recovery path.
 *
 * It reports the outcome rather than only a count because it exists for the
 * case where discovery already failed once: answering `{"operation_count": 0}`
 * to a server that refused the listing again told the operator their retry
 * had worked.
 */
export type AuthConfigOperationsRefreshResponseSchema = {
    auth_config_name: string;
    /**
     * Operations stored for the install. Zero unless status is ok.
     */
    operation_count?: number;
    /**
     * Machine-readable cause when status is not ok: the connector error code for a refused discovery, or why none was attempted.
     */
    reason?: (string | null);
    status: OperationDiscoveryStatus;
};
