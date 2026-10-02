/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecisionNodeConfig } from './DecisionNodeConfig.js';
/**
 * Decision node. Routes to the first rule whose condition is truthy,
 * then asks its question when it has one; falls through to the default
 * outgoing edge when neither picks a node.
 */
export type DecisionNode = {
    config: DecisionNodeConfig;
    id: string;
    label?: (string | null);
    position?: (Record<string, number> | null);
    type?: string;
};
