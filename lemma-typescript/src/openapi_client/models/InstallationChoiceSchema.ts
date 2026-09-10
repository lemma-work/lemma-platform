/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One installation an account could speak for.
 */
export type InstallationChoiceSchema = {
    account_login?: (string | null);
    account_type?: (string | null);
    installation_id: string;
    /**
     * Where the person changes this installation's repository access. A link rather than an API call: the endpoints that add or remove a repository accept only classic personal access tokens.
     */
    manage_url: string;
    /**
     * 'all' or 'selected'. A selected installation does not pick up newly created repositories, which is the commonest reason a repository is missing.
     */
    repository_selection?: (string | null);
};
