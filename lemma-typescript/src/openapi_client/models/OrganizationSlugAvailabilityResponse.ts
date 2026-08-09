/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Organization slug availability response.
 *
 * ``available`` answers only for the slug. When the caller also passes a
 * candidate name, ``name_available`` answers for the globally-unique name; a
 * create succeeds only when both are true.
 */
export type OrganizationSlugAvailabilityResponse = {
    available: boolean;
    name?: (string | null);
    name_available?: (boolean | null);
    slug: string;
};
