/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceConnectorSetupGuide } from './SurfaceConnectorSetupGuide.js';
import type { SurfacePlatform } from './SurfacePlatform.js';
export type SurfacePlatformSetupGuide = {
    connectors?: Array<SurfaceConnectorSetupGuide>;
    docs_path: string;
    platform: SurfacePlatform;
    summary: string;
    title: string;
};
