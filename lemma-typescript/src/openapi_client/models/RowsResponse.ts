/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ReadColumnItem } from './ReadColumnItem.js';
export type RowsResponse = {
    /**
     * The open columns, in order.
     */
    columns: Array<ReadColumnItem>;
    /**
     * Every row's open columns, at most 500, in the order the pod chose. Dates and times are ISO 8601.
     */
    rows: Array<Record<string, (string | number | boolean | null)>>;
    table: string;
};
