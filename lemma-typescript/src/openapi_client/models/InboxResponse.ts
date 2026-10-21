/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChannelResponse } from './ChannelResponse.js';
import type { InboxItem } from './InboxItem.js';
import type { InboxTotals } from './InboxTotals.js';
export type InboxResponse = {
    /**
     * Contacts waiting on a reply, most recently active first: those asking for a person, and those assigned to the reader the contact wrote to last. Only for a reader who may see contacts.
     */
    contacts?: Array<ChannelResponse>;
    /**
     * Unread lines naming the reader, newest first.
     */
    mentions?: Array<InboxItem>;
    totals: InboxTotals;
    /**
     * Questions waiting on the reader, newest first.
     */
    waiting?: Array<InboxItem>;
};
