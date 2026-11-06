/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodOutcome } from './PodOutcome.js';
export type StartedPodResponse = {
    created_at?: (string | null);
    description?: (string | null);
    icon_url?: (string | null);
    id: string;
    /**
     * The name it got, numbered when the one asked for was taken.
     */
    name: string;
    organization_id: string;
    outcome?: (PodOutcome | null);
};
