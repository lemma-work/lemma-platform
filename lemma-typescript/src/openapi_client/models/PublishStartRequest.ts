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
     * Both modes require the repository to exist -- publishing does not create one. CREATE refuses a repository Lemma has already published to (one carrying a publish manifest), so it is what you use for a repository you just made. UPDATE requires that manifest and replaces only Lemma-managed files.
     */
    mode?: PublishMode;
    /**
     * Recorded on the job and reported back, but not acted on: the repository already exists, so its visibility is whatever it was created with.
     */
    private?: boolean;
    /**
     * GitHub repository to publish into, as `name` or `owner/name`. It must already exist and be covered by the Lemma app's installation -- publishing does not create repositories, because a GitHub App cannot.
     */
    repo_name: string;
};
