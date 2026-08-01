/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Whether this org can still put the platform's Lemma-managed bot/number
 * behind a surface.
 *
 * The shared identity is claimable exactly once per organization, so the setup
 * UI can render the option as unavailable *before* the user commits instead of
 * discovering it as a failed save. ``claimed_by_pod_id`` is the pod holding the
 * claim — always a pod in the caller's own org, so linking to it leaks nothing
 * they can't already see.
 */
export type SurfaceSystemClaim = {
    available: boolean;
    claimed_by_pod_id?: (string | null);
    claimed_by_surface_name?: (string | null);
};
