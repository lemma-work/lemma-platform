/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WriteIntent } from './WriteIntent.js';
/**
 * Schema for bulk deleting records.
 */
export type BulkDeleteRecordsRequest = {
    /**
     * Why these rows are written, as DATASTORE triggers see it: `bulk` (the default) or `import` lets each trigger decide by its `bulk_writes` / `import_writes` setting (by default, skipped and recorded); `normal` fires triggers like single writes do.
     */
    intent?: WriteIntent;
    /**
     * Primary key values to delete. At most 1000 per request.
     */
    record_ids: Array<(string | number)>;
};
