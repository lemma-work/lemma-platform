/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { LoopNodeConfig } from './LoopNodeConfig.js';
/**
 * Loop node. Iterates the body chain once per item; the aggregated
 * output is `{results: [...], count: n}` under the loop node id.
 */
export type LoopNode = {
    config: LoopNodeConfig;
    id: string;
    label?: (string | null);
    position?: (Record<string, number> | null);
    type?: string;
};
