/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodRecentsResponse } from './PodRecentsResponse.js';
export type OrganizationRecentsResponse = {
    days: number;
    organization_id: string;
    pods: Array<PodRecentsResponse>;
    /**
     * When the window's first day began.
     */
    since: string;
    timezone: string;
};
