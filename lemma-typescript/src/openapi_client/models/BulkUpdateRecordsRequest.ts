/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WriteIntent } from './WriteIntent.js';
/**
 * Schema for bulk updating records.
 */
export type BulkUpdateRecordsRequest = {
    /**
     * Why these rows are written, as DATASTORE triggers see it: `bulk` (the default) or `import` lets each trigger decide by its `bulk_writes` / `import_writes` setting (by default, skipped and recorded); `normal` fires triggers like single writes do.
     */
    intent?: WriteIntent;
    /**
     * List of record updates. Each item must include the table primary key field. At most 1000 per request.
     */
    records: Array<Record<string, any>>;
};
