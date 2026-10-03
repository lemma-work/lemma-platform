/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfacePlatform } from './SurfacePlatform.js';
/**
 * Pick which surface answers this user for ``platform`` when several could.
 *
 * Exactly one of ``surface_id`` (a surface that already exists) or ``pod_id``
 * (a pod to be answered from on the platform's shared bot, whose surface is
 * made if it has none yet).
 */
export type SetDefaultSurfaceRequest = {
    platform: SurfacePlatform;
    pod_id?: (string | null);
    surface_id?: (string | null);
};
