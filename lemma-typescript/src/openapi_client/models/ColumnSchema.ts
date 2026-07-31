/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DatastoreDataType } from './DatastoreDataType.js';
import type { ForeignKeySpec } from './ForeignKeySpec.js';
/**
 * Schema for a datastore table column.
 */
export type ColumnSchema = {
    /**
     * Whether the column is auto-generated
     */
    auto?: boolean;
    /**
     * Whether this is a computed column
     */
    computed?: boolean;
    /**
     * Default value for the column
     */
    default?: null;
    /**
     * Column description
     */
    description?: (string | null);
    /**
     * SQL expression for computed columns
     */
    expression?: (string | null);
    /**
     * Foreign key specification
     */
    foreign_key?: (ForeignKeySpec | null);
    /**
     * Maximum length for TEXT columns
     */
    max_length?: (number | null);
    /**
     * Column name
     */
    name: string;
    /**
     * Allowed options for ENUM columns
     */
    options?: (Array<string> | null);
    /**
     * Whether the column is required (NOT NULL)
     */
    required?: boolean;
    /**
     * Whether the column is system-managed by the backend.
     */
    system?: boolean;
    /**
     * Column data type
     */
    type: DatastoreDataType;
    /**
     * Additional type-specific parameters
     */
    type_params?: (Record<string, any> | null);
    /**
     * Whether the column must have unique values
     */
    unique?: boolean;
};
