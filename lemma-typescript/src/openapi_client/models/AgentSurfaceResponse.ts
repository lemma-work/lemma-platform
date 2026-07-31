/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentSurfaceStatus } from './AgentSurfaceStatus.js';
import type { SurfaceConfigResponse } from './SurfaceConfigResponse.js';
import type { SurfaceCredentialMode } from './SurfaceCredentialMode.js';
import type { SurfacePlatform } from './SurfacePlatform.js';
import type { SurfaceReach } from './SurfaceReach.js';
export type AgentSurfaceResponse = {
    account_id?: (string | null);
    agent_id?: (string | null);
    agent_name?: (string | null);
    config: SurfaceConfigResponse;
    credential_mode?: SurfaceCredentialMode;
    id: string;
    name: string;
    platform: SurfacePlatform;
    pod_id: string;
    reach?: (SurfaceReach | null);
    status?: AgentSurfaceStatus;
    surface_identity_email?: (string | null);
    surface_identity_id?: (string | null);
    surface_identity_username?: (string | null);
    uses_default_agent?: boolean;
    webhook_url?: (string | null);
};
