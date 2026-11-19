/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ResetRequest = {
    /**
     * Required for `home`, which replaces the machine's disk.
     */
    confirm?: boolean;
    /**
     * With `home`: do not restore the browser's logins onto the new disk. Logins survive every other scope untouched.
     */
    forget_logins?: boolean;
    scopes: Array<'processes' | 'runs' | 'caches' | 'docker' | 'browser-session' | 'home'>;
};
