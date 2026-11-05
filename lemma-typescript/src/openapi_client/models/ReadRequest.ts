/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ReadRequest = {
    /**
     * The newest line the reader has seen.
     */
    seq: number;
    /**
     * Read in this thread: its replies (and its first line) up to `seq` are seen. Without it, the timeline is: thread replies are not.
     */
    thread_root_id?: (string | null);
};
