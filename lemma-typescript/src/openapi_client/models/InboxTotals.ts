/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type InboxTotals = {
    /**
     * Channels with something new.
     */
    channels: number;
    /**
     * Contacts waiting on a reply: asking for a person, or assigned to the reader and written to last by the contact.
     */
    contacts?: number;
    /**
     * Unread lines naming the reader.
     */
    mentions: number;
    /**
     * Questions waiting on the reader's answer.
     */
    waiting: number;
};
