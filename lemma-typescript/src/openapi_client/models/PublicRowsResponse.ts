/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JsonValue } from './JsonValue.js';
import type { PublicColumnItem } from './PublicColumnItem.js';
export type PublicRowsResponse = {
    columns: Array<PublicColumnItem>;
    /**
     * Every row, at most 500. Dates and times are ISO 8601; a JSON column is its own lists and objects.
     */
    rows: Array<Record<string, JsonValue>>;
    table: string;
    /**
     * More than 500 rows matched, so these are the first of them.
     */
    truncated: boolean;
};
