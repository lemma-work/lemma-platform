/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RuntimeModelCapability } from './RuntimeModelCapability.js';
export type RuntimeModelCatalogEntry = {
    capabilities?: Array<RuntimeModelCapability>;
    default_model_settings?: Record<string, any>;
    display_name?: (string | null);
    metadata?: Record<string, any>;
    name: string;
    provider_model_name: string;
};
