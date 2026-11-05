/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ContactIdentityResponse } from './ContactIdentityResponse.js';
export type ContactResponse = {
    /**
     * When an agent first answered them; what the contact limit counts.
     */
    answered_at?: (string | null);
    created_at: string;
    display_name?: (string | null);
    id: string;
    identities?: Array<ContactIdentityResponse>;
    /**
     * Known only by the browser they wrote from: a web visitor nobody has vouched for yet.
     */
    is_anonymous?: boolean;
    /**
     * What to call them: their name, else the handle they are known by (an email address, a number), else that they are a web visitor.
     */
    label: string;
};
