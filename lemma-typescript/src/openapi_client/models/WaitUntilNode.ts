/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WaitUntilNodeConfig } from './WaitUntilNodeConfig.js';
/**
 * Wait node. Suspends the run until the scheduler wakes it.
 */
export type WaitUntilNode = {
    config: WaitUntilNodeConfig;
    id: string;
    label?: (string | null);
    position?: (Record<string, number> | null);
    type?: string;
};
