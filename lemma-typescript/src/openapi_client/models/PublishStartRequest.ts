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
     * GitHub repository to publish into, as `name` or `owner/name`. It must already exist and be covered by the Lemma app's installation -- publishing does not create repositories, because a GitHub App cannot.
     */
    repo_name: string;
};
