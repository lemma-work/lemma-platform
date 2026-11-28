/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GithubInstallationOffer } from './GithubInstallationOffer.js';
import type { GithubWait } from './GithubWait.js';
/**
 * With ``install``: why GitHub is not usable here yet, and the way on.
 */
export type ConnectionGithub = {
    /**
     * With `choose`: the installations the account can see; otherwise the one in question.
     */
    choices?: Array<GithubInstallationOffer>;
    /**
     * With `install`: where the App is installed.
     */
    install_url?: (string | null);
    /**
     * The GitHub account the installation in question is on.
     */
    login?: (string | null);
    manage_url?: (string | null);
    wait: GithubWait;
};
