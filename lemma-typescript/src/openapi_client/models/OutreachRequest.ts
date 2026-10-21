/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { IdentityKind } from './IdentityKind.js';
export type OutreachRequest = {
    /**
     * The same id twice is the same line: a retry sends nothing new.
     */
    client_message_id?: (string | null);
    /**
     * One of the contact's handles to write to: an email address, a phone number (WhatsApp), a Telegram user id.
     */
    handle: string;
    /**
     * Which kind of handle it is, when the value could be two.
     */
    kind?: (IdentityKind | null);
    text: string;
};
