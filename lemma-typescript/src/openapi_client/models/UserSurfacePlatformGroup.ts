/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfacePlatform } from './SurfacePlatform.js';
import type { UserSurfaceItem } from './UserSurfaceItem.js';
/**
 * All of a user's surfaces for one platform. ``conflict`` is true when more
 * than one surface could answer them (they should pick a ``default``).
 */
export type UserSurfacePlatformGroup = {
    conflict?: boolean;
    default_surface_id?: (string | null);
    platform: SurfacePlatform;
    surfaces: Array<UserSurfaceItem>;
};
