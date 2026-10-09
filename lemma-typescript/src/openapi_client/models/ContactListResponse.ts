/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ContactResponse } from './ContactResponse.js';
export type ContactListResponse = {
    items: Array<ContactResponse>;
    /**
     * Pass as `before` for the next page; absent on the last.
     */
    next_before?: (string | null);
};
