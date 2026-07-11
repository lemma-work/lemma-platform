/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Schema for record list response.
 */
export type RecordListResponse = {
    items: Array<Record<string, any>>;
    limit: number;
    next_page_token?: (string | null);
    total?: (number | null);
};
