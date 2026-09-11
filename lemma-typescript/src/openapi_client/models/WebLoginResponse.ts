/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WebLoginStatus } from './WebLoginStatus.js';
export type WebLoginResponse = {
    created_at: string;
    expires_hint_at: (string | null);
    id: string;
    label: string;
    last_used_at: (string | null);
    origin: string;
    status: WebLoginStatus;
    updated_at: string;
    /**
     * Whether the stored session still signs you in. False means it stopped working and the next run will ask you again.
     */
    working: boolean;
};
