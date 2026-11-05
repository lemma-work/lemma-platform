/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentSeat } from './AgentSeat.js';
export type MembersAddRequest = {
    agents?: Array<AgentSeat>;
    /**
     * Members, by user id.
     */
    people?: Array<string>;
};
