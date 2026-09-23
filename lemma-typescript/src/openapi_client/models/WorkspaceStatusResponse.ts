/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type WorkspaceStatusResponse = {
    /**
     * A sentence for a person.
     */
    detail?: (string | null);
    /**
     * `ready`: running. `downloading`: fetching its image, which the first start after an update does. `starting`: coming up. `asleep`: not running, and starts on first use. `unavailable`: could not be asked.
     */
    state: WorkspaceStatusResponse.state;
};
export namespace WorkspaceStatusResponse {
    /**
     * `ready`: running. `downloading`: fetching its image, which the first start after an update does. `starting`: coming up. `asleep`: not running, and starts on first use. `unavailable`: could not be asked.
     */
    export enum state {
        READY = 'ready',
        DOWNLOADING = 'downloading',
        STARTING = 'starting',
        ASLEEP = 'asleep',
        UNAVAILABLE = 'unavailable',
    }
}
