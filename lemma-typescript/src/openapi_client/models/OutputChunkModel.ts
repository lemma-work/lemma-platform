/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OutputChunkModel = {
    channel: OutputChunkModel.channel;
    /**
     * UTF-8, with undecodable bytes replaced.
     */
    data: string;
    sequence: number;
};
export namespace OutputChunkModel {
    export enum channel {
        STDOUT = 'stdout',
        STDERR = 'stderr',
        PTY = 'pty',
    }
}
