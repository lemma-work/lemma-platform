/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceBehaviorConfigInput } from './SurfaceBehaviorConfigInput.js';
import type { SurfaceCredentialMode } from './SurfaceCredentialMode.js';
import type { SurfacePlatform } from './SurfacePlatform.js';
/**
 * Body for `POST /pods/{pod_id}/surfaces` — creates one surface.
 *
 * A pod may have several surfaces of the same ``platform`` (different
 * bots/accounts, each routed to its own agent); ``name`` is the stable,
 * pod-unique identifier used to address it afterward. When omitted, it
 * defaults to the lowercased platform (so the common single-surface-per-
 * platform case needs no name at all) — pick an explicit name to create a
 * second surface of the same platform.
 */
export type SurfaceCreateRequest = {
    account_id?: (string | null);
    config?: SurfaceBehaviorConfigInput;
    credential_mode?: SurfaceCredentialMode;
    default_agent_name?: (string | null);
    is_enabled?: boolean;
    /**
     * Pod-unique surface identifier. Defaults to the lowercased platform.
     */
    name?: (string | null);
    platform: SurfacePlatform;
};
