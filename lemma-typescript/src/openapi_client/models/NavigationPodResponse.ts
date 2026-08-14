/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A pod as a listing entry — enough to draw it, label it, and link to it.
 *
 * The line this payload holds is scalars yes, collections no. A pod's own
 * columns cost nothing to return: they ride along in the query that found the
 * pod, so the response grows with the number of pods and not with what is
 * inside them. Apps, agents and roles are the other side of that line, and
 * live on ``/organizations/{org_id}/home``.
 */
export type NavigationPodResponse = {
    description?: (string | null);
    icon_url?: (string | null);
    id: string;
    name: string;
    updated_at: string;
};
