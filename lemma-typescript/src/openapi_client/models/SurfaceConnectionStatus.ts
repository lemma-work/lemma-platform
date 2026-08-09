/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Health of the account a surface runs on.
 *
 * Mirrors ``AccountStatus`` and adds ``MISSING`` for a surface pointing at an
 * account row that is no longer there. Whether the owner is still in the pod
 * is deliberately *not* folded in here: a departed owner's token keeps working
 * until it expires, so it is a separate fact (``connected_by.is_pod_member``),
 * not a rung on this ladder.
 */
export enum SurfaceConnectionStatus {
    CONNECTED = 'CONNECTED',
    REAUTH_REQUIRED = 'REAUTH_REQUIRED',
    DISCONNECTED = 'DISCONNECTED',
    MISSING = 'MISSING',
}
