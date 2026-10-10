/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PublicColumnItem } from './PublicColumnItem.js';
export type PublicRowsResponse = {
    columns: Array<PublicColumnItem>;
    /**
     * Every row, at most 500. Dates and times are ISO 8601.
     */
    rows: Array<Record<string, (string | number | boolean | null)>>;
    table: string;
};
