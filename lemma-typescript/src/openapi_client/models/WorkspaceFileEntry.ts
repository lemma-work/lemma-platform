/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type WorkspaceFileEntry = {
    /**
     * What this entry is.
     */
    kind: WorkspaceFileEntry.kind;
    /**
     * Last modification time.
     */
    modified_at: string;
    /**
     * Final path segment.
     */
    name: string;
    /**
     * Absolute path inside the workspace.
     */
    path: string;
    /**
     * Size in bytes; 0 for a directory.
     */
    size_bytes: number;
};
export namespace WorkspaceFileEntry {
    /**
     * What this entry is.
     */
    export enum kind {
        FILE = 'file',
        DIRECTORY = 'directory',
        SYMLINK = 'symlink',
    }
}
