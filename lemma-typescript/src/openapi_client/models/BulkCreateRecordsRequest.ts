/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WriteIntent } from './WriteIntent.js';
/**
 * Schema for bulk creating records.
 */
export type BulkCreateRecordsRequest = {
    /**
     * Why these rows are written, as DATASTORE triggers see it: `bulk` (the default) or `import` lets each trigger decide by its `bulk_writes` / `import_writes` setting (by default, skipped and recorded); `normal` fires triggers like single writes do.
     */
    intent?: WriteIntent;
    /**
     * List of record payload objects to insert. At most 1000 per request.
     */
    records: Array<Record<string, any>>;
    /**
     * When true, insert records and update existing rows that conflict on the table primary key.
     */
    upsert?: boolean;
};
