/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MemberConnectStanding } from './MemberConnectStanding.js';
/**
 * A member a required per-member connector is still waiting on.
 *
 * Who, and why -- never which account: nobody but its owner is shown that.
 */
export type MemberAwaited = {
    email?: (string | null);
    name?: (string | null);
    pod_member_id: string;
    standing: MemberConnectStanding;
    user_id: string;
};
