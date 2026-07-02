/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for importing a pod from a public GitHub repo.
 */
export type GithubImportRequest = {
    /**
     * Repo owner (alternative to repo_url).
     */
    owner?: (string | null);
    /**
     * Branch, tag, or commit sha (optional).
     */
    ref?: (string | null);
    /**
     * Repo name (alternative to repo_url).
     */
    repo?: (string | null);
    /**
     * Public repo URL, e.g. https://github.com/owner/repo.
     */
    repo_url?: (string | null);
};

