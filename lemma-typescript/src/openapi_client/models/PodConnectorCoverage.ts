/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MemberAwaited } from './MemberAwaited.js';
/**
 * How many of the members who may use a connector can run it as their own.
 */
export type PodConnectorCoverage = {
    /**
     * Of those, how many have a working account.
     */
    connected: number;
    /**
     * Members of the pod who may use this connector.
     */
    eligible: number;
    /**
     * The pod has more members than were counted.
     */
    truncated?: boolean;
    /**
     * The rest: missing an account, or theirs needs reconnecting.
     */
    waiting_on?: Array<MemberAwaited>;
};
