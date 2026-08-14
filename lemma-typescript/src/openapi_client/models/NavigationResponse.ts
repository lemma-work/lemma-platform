/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NavigationOrganizationResponse } from './NavigationOrganizationResponse.js';
/**
 * Everything a sidebar needs, for every organization, in one response.
 *
 * Deliberately shallow: apps, agents and roles per pod are the detail endpoint's
 * job, because carrying them here would make the payload grow with the content
 * of every organization a person happens to belong to.
 */
export type NavigationResponse = {
    items: Array<NavigationOrganizationResponse>;
};
