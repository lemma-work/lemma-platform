/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RunsAsKind } from './RunsAsKind.js';
/**
 * Who a call by the caller runs as, in words the page can show.
 */
export type ConnectionRunsAs = {
    kind: RunsAsKind;
    /**
     * The caller's account label (me), the shared account's (person), or the App's GitHub organization login(s), comma-separated (app).
     */
    label?: (string | null);
    owner_name?: (string | null);
    /**
     * SHARED: who shared the account.
     */
    owner_user_id?: (string | null);
};
