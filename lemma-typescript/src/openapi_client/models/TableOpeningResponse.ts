/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PublicAudience } from './PublicAudience.js';
import type { PublicColumnResponse } from './PublicColumnResponse.js';
export type TableOpeningResponse = {
    /**
     * How many answers a day it takes, when the member set it; null is the deployment's default.
     */
    answers_per_day: (number | null);
    /**
     * The most answers a day a form may be set to take here.
     */
    answers_per_day_max: number;
    /**
     * Who outside may add rows; null when the table is closed.
     */
    audience: (PublicAudience | null);
    /**
     * The open columns, in order.
     */
    columns: Array<string>;
    /**
     * A confirmed contact's row names them in contact_id.
     */
    contact_owned: boolean;
    /**
     * The columns people outside could be asked to fill.
     */
    offered: Array<PublicColumnResponse>;
    /**
     * Each member sees only their own rows, so it can't be opened.
     */
    per_user: boolean;
    table: string;
};
