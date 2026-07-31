/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceConnectDescriptor } from './SurfaceConnectDescriptor.js';
import type { SurfaceCredentialMode } from './SurfaceCredentialMode.js';
import type { SurfacePlatform } from './SurfacePlatform.js';
/**
 * One connectable surface platform. ``supported_credential_modes`` is the
 * single source of truth for how it can be set up: ``[CUSTOM]`` means an account
 * must be connected; ``[CUSTOM, SYSTEM]`` means a Lemma-managed bot can also run
 * with no account. The frontend derives ``account_needed = SYSTEM not in modes``
 * and ``system_bot_available = SYSTEM in modes``.
 */
export type AvailableSurface = {
    connect?: (SurfaceConnectDescriptor | null);
    connector_available?: boolean;
    connector_id: string;
    description?: (string | null);
    icon?: (string | null);
    platform: SurfacePlatform;
    provider: string;
    supported_credential_modes: Array<SurfaceCredentialMode>;
    title?: (string | null);
};
