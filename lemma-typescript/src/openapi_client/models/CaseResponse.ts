/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AttentionResponse } from './AttentionResponse.js';
import type { CaseEventResponse } from './CaseEventResponse.js';
import type { ContactStatus } from './ContactStatus.js';
import type { PersonRef } from './PersonRef.js';
export type CaseResponse = {
    assigned_at?: (string | null);
    assignee?: (PersonRef | null);
    attention?: (AttentionResponse | null);
    closed_at?: (string | null);
    closed_by?: (PersonRef | null);
    /**
     * Oldest first; the newest 200 of a long case.
     */
    events?: Array<CaseEventResponse>;
    /**
     * The first member line to the contact in it.
     */
    first_response_at?: (string | null);
    first_response_by?: (PersonRef | null);
    id: string;
    opened_at: string;
    /**
     * The channel's newest line when it opened: the case is the lines after.
     */
    opened_seq: number;
    status: ContactStatus;
};
