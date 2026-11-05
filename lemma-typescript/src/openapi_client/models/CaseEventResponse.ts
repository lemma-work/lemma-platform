/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PersonRef } from './PersonRef.js';
export type CaseEventResponse = {
    /**
     * The member who made it, when one did.
     */
    actor?: (PersonRef | null);
    at: string;
    /**
     * `opened`, `assigned`, `handed_back`, `attention`, `acknowledged`, `first_response`, `status`, `closed`, `reopened` or `merged`.
     */
    kind: string;
    /**
     * What it changed to: `status`, `user_id`, `reason`, `seq`, ...
     */
    value?: Record<string, (string | number)>;
};
