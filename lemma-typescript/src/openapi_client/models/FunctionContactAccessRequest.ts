/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Open a function to contacts, or close it.
 */
export type FunctionContactAccessRequest = {
    /**
     * Let a contact's conversation call this function. It runs as the function owner's runs do, held to the function's own grants, and the platform puts the asking contact's `contact_id` in its input.
     */
    contacts_invoke: boolean;
};
