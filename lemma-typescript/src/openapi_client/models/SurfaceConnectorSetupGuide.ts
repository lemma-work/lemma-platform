/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceSetupField } from './SurfaceSetupField.js';
import type { SurfaceSetupMode } from './SurfaceSetupMode.js';
import type { SurfaceSetupStep } from './SurfaceSetupStep.js';
export type SurfaceConnectorSetupGuide = {
    docs_path?: (string | null);
    fields?: Array<SurfaceSetupField>;
    mode: SurfaceSetupMode;
    notes?: Array<string>;
    steps?: Array<SurfaceSetupStep>;
    summary: string;
    supported?: boolean;
    title: string;
};
