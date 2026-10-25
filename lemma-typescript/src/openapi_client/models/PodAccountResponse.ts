/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodAccountSource } from './PodAccountSource.js';
import type { PodAccountVisibility } from './PodAccountVisibility.js';
export type PodAccountResponse = {
    account_id: string;
    allow_sandbox_use?: boolean;
    auth_config_id: string;
    connector_id?: (string | null);
    created_at: string;
    display_name?: (string | null);
    email?: (string | null);
    id: string;
    is_member_default: boolean;
    last_used_at?: (string | null);
    /**
     * Something the change did not do at once, for the owner to read: after turning `allow_sandbox_use` off, that sandboxes already given the token keep a copy until it is swept.
     */
    notice?: (string | null);
    owner_user_id: string;
    pod_id: string;
    source: PodAccountSource;
    /**
     * Slugs of the SHARED pod connectors that run as this account.
     */
    used_by?: Array<string>;
    visibility: PodAccountVisibility;
};
