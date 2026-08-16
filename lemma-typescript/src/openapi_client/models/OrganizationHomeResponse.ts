/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { HomePodResponse } from './HomePodResponse.js';
/**
 * One organization's landing page in a single response.
 */
export type OrganizationHomeResponse = {
    name: string;
    organization_id: string;
    pods: Array<HomePodResponse>;
    role: string;
    slug?: (string | null);
};
