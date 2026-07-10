/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodConfig } from './PodConfig.js';
/**
 * Pod response schema.
 */
export type PodResponse = {
    config?: PodConfig;
    created_at: string;
    description?: (string | null);
    icon_url?: (string | null);
    id: string;
    name: string;
    organization_id: string;
    updated_at: string;
    user_id: string;
};
