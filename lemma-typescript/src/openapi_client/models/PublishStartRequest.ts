/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for publishing a pod to GitHub.
 */
export type PublishStartRequest = {
    /**
     * GitHub connector account to publish as (optional).
     */
    account_id?: (string | null);
    /**
     * Polish the generated README with the system model.
     */
    ai_readme?: boolean;
    /**
     * Create the repo as private.
     */
    private?: boolean;
    /**
     * Name for the new GitHub repo.
     */
    repo_name: string;
};

