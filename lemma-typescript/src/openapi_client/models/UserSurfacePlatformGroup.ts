/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfacePlatform } from './SurfacePlatform.js';
import type { UserSurfaceItem } from './UserSurfaceItem.js';
/**
 * All of a user's surfaces for one platform. ``conflict`` is true when two
 * of them answer at the same address, so the user has to say which pod hears
 * them (the ``shares_address`` surfaces are the ones to choose between).
 */
export type UserSurfacePlatformGroup = {
    conflict?: boolean;
    default_surface_id?: (string | null);
    platform: SurfacePlatform;
    surfaces: Array<UserSurfaceItem>;
};
