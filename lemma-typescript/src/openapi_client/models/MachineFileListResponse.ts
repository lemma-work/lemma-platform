/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MachineFileEntry } from './MachineFileEntry.js';
export type MachineFileListResponse = {
    entries?: Array<MachineFileEntry>;
    exists?: boolean;
    home_root?: string;
    next_after?: (string | null);
    path: string;
    /**
     * The machine is paused and was not woken; ask with `wake=true`.
     */
    sleeping?: boolean;
    truncated?: boolean;
    workspace_root?: string;
};
