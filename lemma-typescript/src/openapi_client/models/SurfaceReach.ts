/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * How a human reaches this surface.
 *
 * ``handle`` is the platform-native name a person types/sees to message the
 * bot (Slack/Teams bot display name, Telegram ``@username``, WhatsApp phone,
 * or the account/email for email surfaces). ``email`` is the surface's email
 * address, when it has one.
 */
export type SurfaceReach = {
    email?: (string | null);
    handle?: (string | null);
};
