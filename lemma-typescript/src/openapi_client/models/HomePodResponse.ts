/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HomeAgentResponse } from './HomeAgentResponse.js';
import type { HomeAppResponse } from './HomeAppResponse.js';
/**
 * A pod with what it contains and what the caller is to it.
 */
export type HomePodResponse = {
    agents: Array<HomeAgentResponse>;
    apps: Array<HomeAppResponse>;
    description?: (string | null);
    icon_url?: (string | null);
    id: string;
    name: string;
    roles: Array<string>;
};
