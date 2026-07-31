/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfacePlatform } from './SurfacePlatform.js';
/**
 * One of the current user's surfaces (across any pod they belong to).
 */
export type UserSurfaceItem = {
    agent_id?: (string | null);
    id: string;
    is_default?: boolean;
    name: string;
    platform: SurfacePlatform;
    pod_id: string;
};
