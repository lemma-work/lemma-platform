/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfacePlatform } from './SurfacePlatform.js';
/**
 * Pick which surface answers this user for ``platform`` when several could.
 */
export type SetDefaultSurfaceRequest = {
    platform: SurfacePlatform;
    surface_id: string;
};
