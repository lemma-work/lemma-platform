/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Why a platform cannot be connected on this server right now.
 *
 * Published so a setup screen can say what to do instead of offering a
 * Connect button whose only outcome is a refusal. Each value names the thing
 * that is missing, not the setting that supplies it: the setting is the
 * operator's word, and on Desktop the person reading is the operator but
 * configures it through a form, not an environment variable.
 */
export enum SurfaceUnavailableReason {
    NEEDS_PUBLIC_LINK = 'NEEDS_PUBLIC_LINK',
    NEEDS_EMAIL_DOMAIN = 'NEEDS_EMAIL_DOMAIN',
}
