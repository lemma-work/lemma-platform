/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecisionNodeConfig } from './DecisionNodeConfig.js';
/**
 * Decision node. Routes to the first rule whose condition is truthy, or on
 * the answer to its question; falls through to the default outgoing edge
 * when nothing routes.
 */
export type DecisionNode = {
    config: DecisionNodeConfig;
    id: string;
    label?: (string | null);
    position?: (Record<string, number> | null);
    type?: string;
};
