/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ContactResponse } from './ContactResponse.js';
export type ContactListResponse = {
    items: Array<ContactResponse>;
    /**
     * Pass as `cursor` for the next page; absent on the last.
     */
    next_cursor?: (string | null);
};
