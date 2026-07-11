/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceChannelRouteResponse } from './SurfaceChannelRouteResponse.js';
import type { SurfaceIdentityConfigResponse } from './SurfaceIdentityConfigResponse.js';
import type { SurfaceSendPolicyConfig } from './SurfaceSendPolicyConfig.js';
/**
 * Mirrors SurfaceBehaviorConfigInput: what you send is what you get back.
 */
export type SurfaceConfigResponse = {
    channels?: Array<SurfaceChannelRouteResponse>;
    dm_conversation_reset_after_hours?: number;
    identity?: SurfaceIdentityConfigResponse;
    send_policy?: SurfaceSendPolicyConfig;
};
