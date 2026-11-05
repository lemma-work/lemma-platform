/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentSeat } from './AgentSeat.js';
import type { Visibility } from './Visibility.js';
export type ChannelCreateRequest = {
    agents?: Array<AgentSeat>;
    /**
     * Members to add, by user id.
     */
    members?: Array<string>;
    title: string;
    topic?: (string | null);
    visibility?: Visibility;
};
