/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Whether the machine is ready and, if not, what it is doing. Never wakes it.
 */
export type MachineStartupResponse = {
    detail?: (string | null);
    disk_total_bytes?: (number | null);
    disk_used_bytes?: (number | null);
    done_mb?: (number | null);
    /**
     * `ready`: running. `downloading`: fetching its image, which the first start after an update does. `starting`: coming up. `asleep`: not running, and starts on first use. `unavailable`: could not be asked.
     */
    state: MachineStartupResponse.state;
    total_mb?: (number | null);
};
export namespace MachineStartupResponse {
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
