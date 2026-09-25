/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OperationExecutionRequest = {
    account_id?: (string | null);
    /**
     * The operation's arguments. A file argument takes a reference -- `{"pod_path": "/me/report.pdf"}`, `{"file_id": "..."}`, `{"url": "https://..."}` or `{"base64": "...", "filename": "..."}` -- read with the caller's own access before the call is made. `output_path` chooses where a file result lands in the pod.
     */
    payload: Record<string, any>;
    /**
     * The pod that `pod_path` and `file_id` references resolve in, and that file results land in. Implied for a call made from inside a pod; name it when calling as a person from outside one.
     */
    pod_id?: (string | null);
};
