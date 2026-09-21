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
     * POSIX permission bits, when the fabric reports them. A viewer showing a file it cannot write should be able to say so.
     */
    mode?: (number | null);
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
     * Content hash, when the fabric computes one. Doubles as the `ETag` on a read, so re-opening a file a viewer already has is a 304 rather than the bytes again.
     */
    sha256?: (string | null);
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
