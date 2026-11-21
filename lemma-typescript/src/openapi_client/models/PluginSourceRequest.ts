/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One plugin: a GitHub repository, and the folder in it its bundle is in.
 */
export type PluginSourceRequest = {
    /**
     * Repository owner.
     */
    owner?: (string | null);
    /**
     * The folder that holds the bundle's `pod.json`, e.g. 'install/mail'. Omit for a repository that is one bundle.
     */
    path?: (string | null);
    /**
     * Branch, tag or sha.
     */
    ref?: (string | null);
    /**
     * Repository name.
     */
    repo?: (string | null);
    /**
     * The repository's URL (alternative to owner+repo).
     */
    repo_url?: (string | null);
};
