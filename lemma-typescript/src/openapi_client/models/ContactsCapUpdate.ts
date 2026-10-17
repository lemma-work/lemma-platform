/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ContactsCapUpdate = {
    /**
     * How many contacts the bots may answer. Leave it out to keep the current number; null removes the limit.
     */
    max_contacts?: (number | null);
    /**
     * Null removes the cap: no limit, kept as the owner's choice. Zero stops bots answering contacts. Leave it out to keep the current cap.
     */
    monthly_limit_usd?: (number | null);
};
