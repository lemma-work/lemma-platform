/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodJoinPolicy } from './PodJoinPolicy.js';
/**
 * A pod the caller may join and is not in: what to show before they do.
 */
export type JoinablePodResponse = {
    description?: (string | null);
    icon_url?: (string | null);
    id: string;
    join_policy: PodJoinPolicy;
    /**
     * How many people are in the pod now.
     */
    member_count: number;
    name: string;
};
