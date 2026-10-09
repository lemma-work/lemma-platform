/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Open a function to contacts, or close it.
 */
export type FunctionContactAccessRequest = {
    /**
     * Let a contact's conversation call this function. It runs as the function itself, with no member behind it: its own grants, its own pod, and only the asking contact's rows of contact-owned tables. The platform puts the contact's `contact_id` in its input, so the input schema must declare `contact_id`.
     */
    contacts_invoke: boolean;
};
