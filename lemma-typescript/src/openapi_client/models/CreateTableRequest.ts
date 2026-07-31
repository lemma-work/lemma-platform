/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ColumnSchema } from './ColumnSchema.js';
/**
 * Schema for creating a new table.
 */
export type CreateTableRequest = {
    /**
     * Table column definitions. Each column name must be unique. Use `type`, `required`, `default`, `foreign_key`, and `computed` as needed. The backend also materializes physical system columns so table metadata reflects the real schema: `id` when omitted as the primary key, `created_at`, `updated_at`, and `user_id` when RLS is enabled.
     */
    columns: Array<ColumnSchema>;
    /**
     * Optional table metadata/configuration. This updates table config metadata and does not directly alter physical columns.
     */
    config?: (Record<string, any> | null);
    /**
     * Enable row-level security for this table. When enabled, API reads/writes are scoped by current user.
     */
    enable_rls?: boolean;
    /**
     * Table name. Use alphanumeric and underscore only. Names prefixed with `reserved_` are system-managed and should not be user-created.
     */
    name: string;
    /**
     * Primary key column name. If not `id`, it must also be declared in `columns`.
     */
    primary_key_column?: string;
    visibility?: (string | null);
};
