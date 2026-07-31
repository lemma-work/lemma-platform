/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodConfig } from './PodConfig.js';
/**
 * Pod creation request schema.
 */
export type PodCreateRequest = {
    config?: PodConfig;
    description?: (string | null);
    icon_url?: (string | null);
    name: string;
    organization_id: string;
};
