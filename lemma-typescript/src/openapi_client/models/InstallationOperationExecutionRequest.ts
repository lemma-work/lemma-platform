/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The deprecated org-level execute's body: an installation, maybe an account.
 */
export type InstallationOperationExecutionRequest = {
    /**
     * Deprecated. Served only if it is the account a pod connector for this installation resolves to, or your own on a per-member one.
     * @deprecated
     */
    account_id?: (string | null);
    /**
     * The operation's arguments. A file argument takes a reference -- `{"pod_path": "/me/report.pdf"}`, `{"file_id": "..."}`, `{"url": "https://..."}` or `{"base64": "...", "filename": "..."}` -- read with the caller's own access before the call is made. `output_path` chooses where a file result lands in the pod.
     */
    payload: Record<string, any>;
    /**
     * The pod whose connectors serve the call, and that file references resolve in. Implied for a call made from inside a pod.
     */
    pod_id?: (string | null);
};
