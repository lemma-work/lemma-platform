/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuthConfigResponseSchema } from './AuthConfigResponseSchema.js';
import type { OperationDiscoverySchema } from './OperationDiscoverySchema.js';
export type AuthConfigUpdateResponseSchema = {
    /**
     * Connected accounts flagged for reconnect because the change invalidated their stored credentials. They are never deleted: the account keeps its id and grants, and reconnecting updates it in place, so anything referencing it keeps working.
     */
    accounts_marked_for_reauth?: number;
    auth_config: AuthConfigResponseSchema;
    /**
     * Operations re-discovered because the change altered where they come from. Zero for a connector whose operations are static, and also zero when discovery was refused -- read `operations_discovery.status` to tell those apart.
     */
    operations_discovered?: number;
    /**
     * Whether the re-discovery this change triggered succeeded.
     */
    operations_discovery: OperationDiscoverySchema;
};
