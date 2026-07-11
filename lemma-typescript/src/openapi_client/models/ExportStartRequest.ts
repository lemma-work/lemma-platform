/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for starting a pod export.
 */
export type ExportStartRequest = {
    /**
     * Opt-in per-table seed selection: include row data only for these named tables (the common case — ship a few setup/config tables' rows). Ignored for names that aren't real tables (a warning is surfaced). Combined with `with_data` as a union; omit both for a resources-only export.
     */
    data_tables?: (Array<string> | null);
    /**
     * Optional list of resource types to include (e.g. ['tables', 'agents']). Omit to export every supported resource type.
     */
    include?: (Array<string> | null);
    /**
     * Requested lifetime (seconds) of the signed download URL + archive retention. Clamped to the configured maximum; omit for the default.
     */
    ttl_seconds?: (number | null);
    /**
     * Opt-in: include row data for EVERY table (data.csv per table) as seed/default data. Off by default — an export carries only pod resources, which recreate the pod in an empty-table state. Prefer `data_tables` to seed only specific setup tables; enable this only to seed the whole pod. Row data is capped (per-table + total) regardless.
     */
    with_data?: boolean;
    /**
     * Opt-in: include the pod's POD-visible file storage (folders + file bytes) in the bundle. Off by default. File bytes share a conservative size budget with table row data (meant for small skill/script/seed files, not a bulk file dump).
     */
    with_files?: boolean;
};
