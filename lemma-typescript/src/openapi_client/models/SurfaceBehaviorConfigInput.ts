/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceChannelRouteInput } from './SurfaceChannelRouteInput.js';
import type { SurfaceIdentityConfigInput } from './SurfaceIdentityConfigInput.js';
import type { SurfaceSendPolicyConfig } from './SurfaceSendPolicyConfig.js';
export type SurfaceBehaviorConfigInput = {
    channels?: Array<SurfaceChannelRouteInput>;
    dm_conversation_reset_after_hours?: number;
    identity?: SurfaceIdentityConfigInput;
    send_policy?: SurfaceSendPolicyConfig;
};
