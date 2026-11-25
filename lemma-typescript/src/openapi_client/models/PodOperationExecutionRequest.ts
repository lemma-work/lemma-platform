/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A pod connector call's body: its arguments, and who it presents as.
 */
export type PodOperationExecutionRequest = {
    /**
     * Who the call presents as. `user` (the default) is the account's own person. `app` is the connector's app -- for GitHub, the Lemma App's bot -- and only a function run may ask for it: anyone else is refused with `APP_IDENTITY_FUNCTIONS_ONLY`. It is the app or nothing: an operation only a person may call, an account with no App installation, or a connector with no app is refused with `APP_IDENTITY_UNAVAILABLE` before anything is sent.
     */
    act_as?: PodOperationExecutionRequest.act_as;
    /**
     * The operation's arguments. A file argument takes a reference -- `{"pod_path": "/me/report.pdf"}`, `{"file_id": "..."}`, `{"url": "https://..."}` or `{"base64": "...", "filename": "..."}` -- read with the caller's own access before the call is made. `output_path` chooses where a file result lands in the pod.
     */
    payload: Record<string, any>;
};
export namespace PodOperationExecutionRequest {
    /**
     * Who the call presents as. `user` (the default) is the account's own person. `app` is the connector's app -- for GitHub, the Lemma App's bot -- and only a function run may ask for it: anyone else is refused with `APP_IDENTITY_FUNCTIONS_ONLY`. It is the app or nothing: an operation only a person may call, an account with no App installation, or a connector with no app is refused with `APP_IDENTITY_UNAVAILABLE` before anything is sent.
     */
    export enum act_as {
        USER = 'user',
        APP = 'app',
    }
}
