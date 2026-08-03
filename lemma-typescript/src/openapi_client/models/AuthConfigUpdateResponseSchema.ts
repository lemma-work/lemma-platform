/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuthConfigResponseSchema } from './AuthConfigResponseSchema.js';
export type AuthConfigUpdateResponseSchema = {
    /**
     * Connected accounts flagged for reconnect because the change invalidated their stored credentials. They are never deleted: the account keeps its id and grants, and reconnecting updates it in place, so anything referencing it keeps working.
     */
    accounts_marked_for_reauth?: number;
    auth_config: AuthConfigResponseSchema;
    /**
     * Operations re-discovered because the change altered where they come from. Zero for a connector whose operations are static.
     */
    operations_discovered?: number;
};
