/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkspaceFileEntry } from './WorkspaceFileEntry.js';
export type WorkspaceFileListResponse = {
    entries?: Array<WorkspaceFileEntry>;
    /**
     * False when the directory is not there. A directory that does not exist and one that is merely empty used to answer identically, which is why a pane pointed at the wrong path looked like a working, empty folder rather than a mistake.
     */
    exists?: boolean;
    /**
     * Pass as `after` to get the next page. Null when this is the last one. A directory with more entries than fit was previously a dead end: the rest could be counted and never reached.
     */
    next_after?: (string | null);
    /**
     * The directory that was listed.
     */
    path: string;
    /**
     * True when the workspace is paused and was not woken to answer. Entries are empty; ask again with `wake=true` to start it.
     */
    sleeping?: boolean;
    /**
     * True when the directory holds more entries than were returned.
     */
    truncated?: boolean;
};
