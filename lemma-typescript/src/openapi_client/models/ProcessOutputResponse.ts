/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutputChunkModel } from './OutputChunkModel.js';
export type ProcessOutputResponse = {
    chunks: Array<OutputChunkModel>;
    exit_code?: (number | null);
    next_sequence: number;
    state: string;
    truncated_before_sequence?: (number | null);
};
