/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ContactResponse } from './ContactResponse.js';
import type { ContactRow } from './ContactRow.js';
import type { ExportedConversation } from './ExportedConversation.js';
/**
 * Everything the pod holds about one contact, a page at a time.
 *
 * Their conversations come first, then their rows in the pod's
 * contact-owned tables. Follow `next_cursor` until it is absent.
 */
export type ContactExportResponse = {
    contact: ContactResponse;
    conversations: Array<ExportedConversation>;
    /**
     * Pass as `cursor` for the next page; absent on the last.
     */
    next_cursor?: (string | null);
    rows?: Array<ContactRow>;
};
