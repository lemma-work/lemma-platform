/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PublicAudience } from './PublicAudience.js';
export type OpenReadsRequest = {
    audience: PublicAudience;
    /**
     * The columns people outside may see, in the order to show them. Checked against the table when it is opened.
     */
    columns: Array<string>;
    /**
     * One of the open columns, to read rows in ascending order of.
     */
    order_by?: (string | null);
};
