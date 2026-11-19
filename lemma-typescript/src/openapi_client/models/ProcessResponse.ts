/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type ProcessResponse = {
    command: string;
    cwd: string;
    exit_code?: (number | null);
    id: string;
    run_id?: (string | null);
    service?: (string | null);
    started_at?: (string | null);
    started_by?: (string | null);
    state: ProcessResponse.state;
    tty?: boolean;
};
export namespace ProcessResponse {
    export enum state {
        RESERVED = 'reserved',
        STARTING = 'starting',
        UNKNOWN = 'unknown',
        RUNNING = 'running',
        SUCCEEDED = 'succeeded',
        FAILED = 'failed',
        CANCELLED = 'cancelled',
        TIMED_OUT = 'timed_out',
    }
}
