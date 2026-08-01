/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SendAudience } from './SendAudience.js';
/**
 * Proactive-send controls. Mirrored across request and response.
 *
 * ``audience`` is the field to set. ``allow_send`` is the original boolean,
 * still accepted so existing clients and stored bundles keep working: it maps
 * to ``SELF`` / ``NOBODY``. When both are present, ``audience`` wins.
 */
export type SurfaceSendPolicyConfig = {
    allow_send?: (boolean | null);
    audience?: SendAudience;
    max_messages_per_recipient_per_hour?: number;
};
