/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ResetScopeResult = {
    detail?: (string | null);
    freed_bytes?: (number | null);
    scope: ResetScopeResult.scope;
};
export namespace ResetScopeResult {
    export enum scope {
        PROCESSES = 'processes',
        RUNS = 'runs',
        CACHES = 'caches',
        DOCKER = 'docker',
        BROWSER_SESSION = 'browser-session',
        HOME = 'home',
    }
}
