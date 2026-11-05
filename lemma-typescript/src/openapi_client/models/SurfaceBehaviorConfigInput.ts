/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceChannelRouteInput } from './SurfaceChannelRouteInput.js';
import type { SurfaceContactsConfig } from './SurfaceContactsConfig.js';
import type { SurfaceGroupsConfig } from './SurfaceGroupsConfig.js';
import type { SurfaceIdentityConfigInput } from './SurfaceIdentityConfigInput.js';
import type { SurfaceSendPolicyConfig } from './SurfaceSendPolicyConfig.js';
import type { SurfaceSlackConfigInput } from './SurfaceSlackConfigInput.js';
import type { SurfaceTelegramConfigInput } from './SurfaceTelegramConfigInput.js';
import type { SurfaceWhatsAppConfigInput } from './SurfaceWhatsAppConfigInput.js';
export type SurfaceBehaviorConfigInput = {
    channels?: Array<SurfaceChannelRouteInput>;
    contacts?: SurfaceContactsConfig;
    /**
     * Ignored. A private chat is one conversation per agent and person, however long it goes quiet. Still accepted so existing pod bundles and clients keep working.
     * @deprecated
     */
    dm_conversation_reset_after_hours?: (number | null);
    groups?: SurfaceGroupsConfig;
    identity?: SurfaceIdentityConfigInput;
    send_policy?: SurfaceSendPolicyConfig;
    slack?: SurfaceSlackConfigInput;
    telegram?: SurfaceTelegramConfigInput;
    whatsapp?: SurfaceWhatsAppConfigInput;
};
