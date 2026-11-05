/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NotifyLevel } from './NotifyLevel.js';
/**
 * Absent is unchanged; ``null`` clears ``notify_via`` (the pod's rule)
 * and ``muted_until`` (unmuted).
 */
export type MyMembershipUpdate = {
    muted_until?: (string | null);
    notify?: (NotifyLevel | null);
    notify_via?: (Array<'EMAIL' | 'SLACK' | 'TEAMS' | 'WHATSAPP' | 'TELEGRAM' | 'RESEND'> | null);
};
