/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkAnswerResponse } from './WorkAnswerResponse.js';
/**
 * User response schema.
 */
export type UserResponse = {
    country?: (string | null);
    created_at: string;
    date_of_birth?: (string | null);
    deactivated_at?: (string | null);
    deactivation_reason?: (string | null);
    email: string;
    email_verified_at?: (string | null);
    first_name?: (string | null);
    id: string;
    is_active: boolean;
    is_superuser: boolean;
    is_verified: boolean;
    last_name?: (string | null);
    mobile_number?: (string | null);
    mobile_verified_at?: (string | null);
    timezone?: (string | null);
    updated_at: string;
    /**
     * What they said they do, or null until they have been asked. An answer with no jobs and no `other` means they skipped it. Only on the current user's own record.
     */
    work?: (WorkAnswerResponse | null);
};
