/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceBehaviorConfigInput } from './SurfaceBehaviorConfigInput.js';
import type { SurfaceCredentialMode } from './SurfaceCredentialMode.js';
/**
 * Body for `PATCH /pods/{pod_id}/surfaces/{surface_name}`.
 *
 * Partial update (merge semantics): only fields present in the request are
 * applied. The surface's ``platform`` and ``name`` are immutable — delete and
 * recreate to change either.
 */
export type SurfaceUpdateRequest = {
    account_id?: (string | null);
    config?: SurfaceBehaviorConfigInput;
    credential_mode?: (SurfaceCredentialMode | null);
    default_agent_name?: (string | null);
    is_enabled?: (boolean | null);
};
