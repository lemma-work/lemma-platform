/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Schema for bulk updating records.
 */
export type BulkUpdateRecordsRequest = {
    /**
     * List of record updates. Each item must include the table primary key field.
     */
    records: Array<Record<string, any>>;
};
