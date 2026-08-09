/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceConnectionOwner } from './SurfaceConnectionOwner.js';
import type { SurfaceConnectionStatus } from './SurfaceConnectionStatus.js';
/**
 * Which account a surface runs on, and who connected it.
 *
 * Accounts are personal (``accounts.user_id``) while surfaces belong to the
 * pod, so ``account_id`` alone answers nothing for a teammate — they cannot
 * resolve an id they don't own. This block is the pod-visible *identity* of
 * that account: enough for any editor to see who to ask, never the credential.
 */
export type SurfaceConnection = {
    account_id: string;
    connected_by?: (SurfaceConnectionOwner | null);
    connector_id: string;
    display_name?: (string | null);
    status?: SurfaceConnectionStatus;
};
