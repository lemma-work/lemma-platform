/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Schema for executing read-only SQL within a datastore.
 */
export type DatastoreQueryRequest = {
    /**
     * Read-only SQL query executed inside this datastore schema. A single SELECT statement only; mutating statements (INSERT, UPDATE, DELETE, ALTER, DROP, CREATE, TRUNCATE, ...) and cross-schema references are rejected. Joins, aggregates, and subqueries across tables are allowed, including RLS-enabled tables — rows of an RLS table are scoped to the caller unless they administer it. Example: `SELECT id, amount FROM expenses WHERE amount > 100 ORDER BY created_at DESC LIMIT 20`.
     */
    query: string;
};
