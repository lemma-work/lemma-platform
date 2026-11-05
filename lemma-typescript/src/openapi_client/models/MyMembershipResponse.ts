/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MemberRole } from './MemberRole.js';
import type { NotifyLevel } from './NotifyLevel.js';
export type MyMembershipResponse = {
    /**
     * Whether the reader may change this channel and its members.
     */
    can_manage?: boolean;
    joined?: boolean;
    muted_until?: (string | null);
    notify?: NotifyLevel;
    /**
     * Where lines reach them outside the app; absent for the pod's rule.
     */
    notify_via?: (Array<string> | null);
    read_seq?: number;
    role?: (MemberRole | null);
};
