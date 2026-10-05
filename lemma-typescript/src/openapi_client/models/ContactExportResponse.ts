/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ContactResponse } from './ContactResponse.js';
import type { ExportedConversation } from './ExportedConversation.js';
/**
 * Everything the pod holds about one contact, for a request to see it.
 */
export type ContactExportResponse = {
    contact: ContactResponse;
    conversations: Array<ExportedConversation>;
};
