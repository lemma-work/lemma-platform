/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { JsonObject } from './JsonObject.js';
/**
 * Request to execute a function.
 */
export type ExecuteFunctionRequest = {
    input_data?: JsonObject;
    /**
     * Run a specific revision instead of the live one -- a revision number ('r12') or a hash prefix. Requires function.update: running a superseded build is an authoring action, not an execution one.
     */
    revision?: (string | null);
};
