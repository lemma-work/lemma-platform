/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GranteeRef } from './GranteeRef.js';
import type { PodAccountVisibility } from './PodAccountVisibility.js';
export type PodAccountUpdateRequest = {
    /**
     * Owner only: allow this account's credential inside sandboxes, so a SHARED connector on it (the pod's `github`) can give other members' workspaces its raw token for git and gh, which they can copy. Off by default; withdrawing the account turns it off.
     */
    allow_sandbox_use?: (boolean | null);
    /**
     * Owner only: make this the account your own calls use here.
     */
    is_member_default?: (boolean | null);
    /**
     * RESTRICTED only: replaces who may use it.
     */
    share_with?: (Array<GranteeRef> | null);
    /**
     * Owner only. PERSONAL withdraws it: SHARED connectors using it need another account at once.
     */
    visibility?: (PodAccountVisibility | null);
};
