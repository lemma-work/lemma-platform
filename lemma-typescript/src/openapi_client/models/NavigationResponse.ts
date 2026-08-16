/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NavigationOrganizationResponse } from './NavigationOrganizationResponse.js';
/**
 * Everything a sidebar and a pod list need, for every organization, at once.
 *
 * Shallow in the sense that matters: it carries each pod's own columns, and
 * nothing that would require looking inside a pod. Apps, agents and roles are
 * the detail endpoint's job, because carrying them here would make the payload
 * grow with the content of every organization a person happens to belong to,
 * which is precisely the cost this endpoint exists to remove.
 */
export type NavigationResponse = {
    items: Array<NavigationOrganizationResponse>;
};
