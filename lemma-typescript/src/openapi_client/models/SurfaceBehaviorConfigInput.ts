/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceChannelRouteInput } from './SurfaceChannelRouteInput.js';
import type { SurfaceIdentityConfigInput } from './SurfaceIdentityConfigInput.js';
import type { SurfaceSendPolicyConfig } from './SurfaceSendPolicyConfig.js';
import type { SurfaceSlackConfigInput } from './SurfaceSlackConfigInput.js';
import type { SurfaceTelegramConfigInput } from './SurfaceTelegramConfigInput.js';
export type SurfaceBehaviorConfigInput = {
    channels?: Array<SurfaceChannelRouteInput>;
    /**
     * Ignored. The DM reset window is a deployment-wide setting (SURFACE_DM_CONVERSATION_RESET_AFTER_HOURS). Still accepted so existing pod bundles and clients keep working.
     * @deprecated
     */
    dm_conversation_reset_after_hours?: (number | null);
    identity?: SurfaceIdentityConfigInput;
    send_policy?: SurfaceSendPolicyConfig;
    slack?: SurfaceSlackConfigInput;
    telegram?: SurfaceTelegramConfigInput;
};
