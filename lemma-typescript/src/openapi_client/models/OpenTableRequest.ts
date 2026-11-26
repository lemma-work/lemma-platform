/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PublicAudience } from './PublicAudience.js';
export type OpenTableRequest = {
    /**
     * How many answers a day it takes, up to `answers_per_day_max`; null is the deployment's default. Omitted, it stays as it is.
     */
    answers_per_day?: (number | null);
    audience: PublicAudience;
    /**
     * The columns people outside may fill, in the order to ask them. Checked against the table when it is opened.
     */
    columns: Array<string>;
};
