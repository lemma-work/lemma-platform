/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentRuntimeConfig } from './AgentRuntimeConfig.js';
import type { PodContactSettings } from './PodContactSettings.js';
import type { PodJoinPolicy } from './PodJoinPolicy.js';
import type { PodOutcome } from './PodOutcome.js';
import type { PodRecipe } from './PodRecipe.js';
import type { PodTriggerSettings } from './PodTriggerSettings.js';
/**
 * Typed pod-level configuration.
 */
export type PodConfig = {
    /**
     * Everyone who joins the organization from now on becomes a member of this pod, as a pod user. Needs join_policy ORG_MEMBERS or PUBLIC, and the authority to change the join policy. People already in the organization are not added.
     */
    auto_join_new_members?: boolean;
    contacts?: (PodContactSettings | null);
    default_profile_id?: (string | null);
    default_runtime?: (AgentRuntimeConfig | null);
    join_policy?: PodJoinPolicy;
    outcome?: (PodOutcome | null);
    recipes?: Array<PodRecipe>;
    triggers?: (PodTriggerSettings | null);
};
