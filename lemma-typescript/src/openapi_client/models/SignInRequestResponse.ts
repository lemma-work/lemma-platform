/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SignInRequestStatus } from './SignInRequestStatus.js';
export type SignInRequestResponse = {
    created_at: string;
    id: string;
    origin: string;
    reason: string;
    /**
     * Whether the login was kept for next time.
     */
    saved?: boolean;
    /**
     * Why it was not kept, in words, when it was not.
     */
    saved_detail?: (string | null);
    status: SignInRequestStatus;
};
