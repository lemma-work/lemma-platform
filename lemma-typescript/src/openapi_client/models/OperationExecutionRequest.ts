/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OperationExecutionRequest = {
    /**
     * The operation's arguments. A file argument takes a reference -- `{"pod_path": "/me/report.pdf"}`, `{"file_id": "..."}`, `{"url": "https://..."}` or `{"base64": "...", "filename": "..."}` -- read with the caller's own access before the call is made. `output_path` chooses where a file result lands in the pod.
     */
    payload: Record<string, any>;
};
