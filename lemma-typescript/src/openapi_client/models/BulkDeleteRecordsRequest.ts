/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Schema for bulk deleting records.
 */
export type BulkDeleteRecordsRequest = {
    /**
     * Primary key values to delete. At most 1000 per request.
     */
    record_ids: Array<(string | number)>;
};
