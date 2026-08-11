/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for starting a pod export.
 */
export type ExportStartRequest = {
    /**
     * Tables whose rows to seed into the bundle, named one by one. There is deliberately no 'every table' switch: row data is the part of a pod most likely to be private, so it leaves the pod only for tables the caller actually asked for. Omit for a bundle with no row data. A name that is not a table in this pod is skipped with a warning. Row data is capped (per-table and in total) regardless.
     */
    data_tables?: (Array<string> | null);
    /**
     * Folder paths whose contents to include, named one by one (e.g. ['/reports', '/config']). Each is exported with everything beneath it. As with `data_tables` there is no 'every folder' switch. Omit for a bundle with no files. A path that is not a folder in this pod is skipped with a warning. File bytes share a conservative size budget with table row data.
     */
    file_folders?: (Array<string> | null);
    /**
     * Optional list of resource types to include (e.g. ['tables', 'agents']). Omit to export every supported resource type.
     */
    include?: (Array<string> | null);
    /**
     * Requested lifetime (seconds) of the signed download URL + archive retention. Clamped to the configured maximum; omit for the default.
     */
    ttl_seconds?: (number | null);
};
