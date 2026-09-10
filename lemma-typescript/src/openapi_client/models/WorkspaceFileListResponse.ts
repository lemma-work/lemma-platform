/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkspaceFileEntry } from './WorkspaceFileEntry.js';
export type WorkspaceFileListResponse = {
    entries?: Array<WorkspaceFileEntry>;
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
