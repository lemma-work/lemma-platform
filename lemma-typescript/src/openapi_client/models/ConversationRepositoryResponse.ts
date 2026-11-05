/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A GitHub repository a conversation can be opened in.
 */
export type ConversationRepositoryResponse = {
    default_branch?: (string | null);
    /**
     * 'owner/name', the value create takes.
     */
    full_name: string;
    name: string;
    owner: string;
    private?: boolean;
    /**
     * The checkout a conversation opened in it works in. Shared by every conversation of yours in this repository.
     */
    workspace_cwd: string;
};
