/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Schema for bulk creating records.
 */
export type BulkCreateRecordsRequest = {
    /**
     * List of record payload objects to insert.
     */
    records: Array<Record<string, any>>;
    /**
     * When true, insert records and update existing rows that conflict on the table primary key.
     */
    upsert?: boolean;
};
