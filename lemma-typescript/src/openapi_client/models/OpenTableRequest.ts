/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PublicAudience } from './PublicAudience.js';
export type OpenTableRequest = {
    audience: PublicAudience;
    /**
     * The columns people outside may fill, in the order to ask them. Checked against the table when it is opened.
     */
    columns: Array<string>;
};
