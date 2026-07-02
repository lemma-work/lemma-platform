/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A record of a bundle installed into this pod (the durable trace of an
 * import; the ephemeral import job state is not kept). ``kind`` distinguishes an
 * uploaded bundle from a GitHub-sourced one; ``repo_url`` is set for GitHub.
 */
export type PodRecipe = {
    format_version?: (number | null);
    imported_at: string;
    imported_by: string;
    kind: string;
    name?: (string | null);
    repo_url?: (string | null);
};

