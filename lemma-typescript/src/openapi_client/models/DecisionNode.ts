/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecisionNodeConfig } from './DecisionNodeConfig.js';
/**
 * Decision node. Routes to the first rule whose condition is truthy;
 * falls through to the default outgoing edge when no rule matches.
 */
export type DecisionNode = {
    config: DecisionNodeConfig;
    id: string;
    label?: (string | null);
    position?: (Record<string, number> | null);
    type?: string;
};
