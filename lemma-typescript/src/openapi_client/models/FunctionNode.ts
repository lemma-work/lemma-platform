/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FunctionNodeConfig } from './FunctionNodeConfig.js';
/**
 * Function node. Completes inline for synchronous functions or waits on
 * the function run for asynchronous ones.
 */
export type FunctionNode = {
    config: FunctionNodeConfig;
    id: string;
    label?: (string | null);
    position?: (Record<string, number> | null);
    type?: string;
};
