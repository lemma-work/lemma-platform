/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ColumnSchema } from './ColumnSchema.js';
/**
 * Schema for adding a column to a table.
 */
export type AddColumnRequest = {
    /**
     * Column definition to append to the table. Existing column names cannot be reused.
     */
    column: ColumnSchema;
};
