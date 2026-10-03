/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AvailablePodItem } from './AvailablePodItem.js';
import type { SurfacePlatform } from './SurfacePlatform.js';
import type { UserSurfaceItem } from './UserSurfaceItem.js';
/**
 * All of a user's surfaces for one platform. ``conflict`` is true when two
 * of them answer at the same address, so the user has to say which pod hears
 * them (the ``shares_address`` surfaces are the ones to choose between).
 *
 * On a platform with a shared bot (WhatsApp), ``available_pods`` lists every
 * pod the user may be answered from -- including pods with no surface there
 * yet -- and ``default_pod_id`` names the one that answers now. The group is
 * present even when the user has no surface on the platform at all.
 */
export type UserSurfacePlatformGroup = {
    available_pods?: Array<AvailablePodItem>;
    conflict?: boolean;
    default_pod_id?: (string | null);
    default_surface_id?: (string | null);
    platform: SurfacePlatform;
    surfaces: Array<UserSurfaceItem>;
};
