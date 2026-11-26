/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GithubBot } from './GithubBot.js';
import type { PodConnectionItem } from './PodConnectionItem.js';
export type PodConnectionsResponse = {
    /**
     * Null when the organization has no GitHub App installation an APP connector can use: proven, and on a GitHub organization.
     */
    github_bot?: (GithubBot | null);
    items: Array<PodConnectionItem>;
    /**
     * The caller holds pod.connector.manage.
     */
    may_manage: boolean;
};
