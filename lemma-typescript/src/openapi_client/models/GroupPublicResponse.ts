/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PublicFileResponse } from './PublicFileResponse.js';
/**
 * The pod's Public files and tables, the first few of each.
 */
export type GroupPublicResponse = {
    files: Array<PublicFileResponse>;
    /**
     * More is Public than is listed here.
     */
    more: boolean;
    tables: Array<string>;
};
