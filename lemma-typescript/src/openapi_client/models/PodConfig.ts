/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRuntimeConfig } from './AgentRuntimeConfig.js';
import type { PodJoinPolicy } from './PodJoinPolicy.js';
import type { PodRecipe } from './PodRecipe.js';
/**
 * Typed pod-level configuration.
 */
export type PodConfig = {
    default_profile_id?: (string | null);
    default_runtime?: (AgentRuntimeConfig | null);
    join_policy?: PodJoinPolicy;
    recipes?: Array<PodRecipe>;
};

