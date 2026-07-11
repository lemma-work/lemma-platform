/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceSetupFieldSource } from './SurfaceSetupFieldSource.js';
export type SurfaceSetupField = {
    description: string;
    example?: (string | null);
    label: string;
    name: string;
    required?: boolean;
    secret?: boolean;
    source: SurfaceSetupFieldSource;
};
