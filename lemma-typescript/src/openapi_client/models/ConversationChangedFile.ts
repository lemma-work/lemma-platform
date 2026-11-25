/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ConversationChangedFile = {
    additions: number;
    /**
     * Git found no lines to count; additions and deletions are 0.
     */
    binary: boolean;
    deletions: number;
    /**
     * Where a renamed file was, before it moved.
     */
    old_path?: (string | null);
    path: string;
    status: ConversationChangedFile.status;
};
export namespace ConversationChangedFile {
    export enum status {
        ADDED = 'added',
        MODIFIED = 'modified',
        DELETED = 'deleted',
        RENAMED = 'renamed',
        UNTRACKED = 'untracked',
    }
}
