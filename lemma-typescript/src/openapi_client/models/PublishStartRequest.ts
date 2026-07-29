/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PublishMode } from './PublishMode.js';
/**
 * Body for publishing a pod to GitHub.
 */
export type PublishStartRequest = {
    /**
     * GitHub connector account to publish as.
     */
    account_id: string;
    /**
     * Polish the generated README with the system model.
     */
    ai_readme?: boolean;
    /**
     * CREATE refuses an existing repository. UPDATE requires an existing repository and replaces only Lemma-managed files.
     */
    mode?: PublishMode;
    /**
     * Create the repo as private.
     */
    private?: boolean;
    /**
     * GitHub repository name (letters, numbers, dot, dash, underscore).
     */
    repo_name: string;
};
