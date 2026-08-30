/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The platforms a pod can be reached on.
 *
 * Email is Resend, and only Resend. Gmail and Outlook were here as
 * Composio-backed mailboxes, which made "an email surface" mean three
 * different transports with three attachment strategies between them -- bytes,
 * Graph drafts, and a signed URL the provider downloads server-side. Reaching
 * a Gmail *account* is still something an agent does, through the connector;
 * it is just not a surface.
 */
export enum SurfacePlatform {
    SLACK = 'SLACK',
    TEAMS = 'TEAMS',
    WHATSAPP = 'WHATSAPP',
    TELEGRAM = 'TELEGRAM',
    RESEND = 'RESEND',
}
