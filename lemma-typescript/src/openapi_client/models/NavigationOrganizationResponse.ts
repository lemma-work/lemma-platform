/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NavigationPodResponse } from './NavigationPodResponse.js';
/**
 * An organization and the pods the caller can see inside it.
 */
export type NavigationOrganizationResponse = {
    id: string;
    name: string;
    pods: Array<NavigationPodResponse>;
    role: string;
    slug?: (string | null);
};
