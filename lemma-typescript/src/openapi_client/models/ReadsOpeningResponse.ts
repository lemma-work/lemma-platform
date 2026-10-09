/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PublicAudience } from './PublicAudience.js';
import type { ReadColumnResponse } from './ReadColumnResponse.js';
export type ReadsOpeningResponse = {
    /**
     * Who outside may read it; null when it is closed.
     */
    audience: (PublicAudience | null);
    /**
     * The open columns, in order.
     */
    columns: Array<string>;
    /**
     * Each row is one contact's, so it can't be opened.
     */
    contact_owned: boolean;
    /**
     * The columns people outside could be shown.
     */
    offered: Array<ReadColumnResponse>;
    /**
     * The open column rows are read in ascending order of.
     */
    order_by: (string | null);
    /**
     * Each member sees only their own rows, so it can't be opened.
     */
    per_user: boolean;
    table: string;
    /**
     * It takes rows from outside, so it can't be opened for reads.
     */
    takes_rows: boolean;
};
