/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Organization slug availability response.
 *
 * ``available`` answers for the slug, which is the handle and is unique across
 * the deployment. It is the only field that can refuse a create.
 *
 * ``name_available`` is answered whenever a candidate name is passed, and is
 * now always ``true``: display names are labels and two organizations may
 * share one (PS-ONB-014). Kept so callers that probe both fields keep one
 * response shape, and deprecated -- do not gate a create on it.
 */
export type OrganizationSlugAvailabilityResponse = {
    available: boolean;
    name?: (string | null);
    /**
     * Always true when a name is supplied: organization display names are not unique. Gate creates on `available` instead.
     * @deprecated
     */
    name_available?: (boolean | null);
    slug: string;
};
