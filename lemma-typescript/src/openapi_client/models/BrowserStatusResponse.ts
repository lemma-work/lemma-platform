/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * What the pane can say without waking anything.
 *
 * `asleep` the computer is paused or was never started; `stopped` it is up but
 * the browser is not (the resting state after five idle minutes); `running` a
 * browser is there now; `unavailable` the relay did not answer, which on an
 * older image is permanent until it is replaced; `unsupported` this fabric
 * cannot reach a port at all.
 */
export type BrowserStatusResponse = {
    detail?: (string | null);
    state: string;
};
