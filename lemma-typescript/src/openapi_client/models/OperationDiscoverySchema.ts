/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OperationDiscoveryStatus } from './OperationDiscoveryStatus.js';
/**
 * What re-reading an install's operation list actually did.
 *
 * `operation_count` alone cannot say: a connector with no operations to
 * advertise, a kind whose operations are static, and a server that refused
 * the listing all report zero. They need different things from the reader --
 * nothing, nothing, and a retry once the server is reachable -- so the status
 * is the field to branch on and the count is detail.
 */
export type OperationDiscoverySchema = {
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
