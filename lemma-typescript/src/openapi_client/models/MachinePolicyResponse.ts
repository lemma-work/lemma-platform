/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SizeOffer } from './SizeOffer.js';
/**
 * What a person or pod may create, for a screen to offer and no more.
 */
export type MachinePolicyResponse = {
    default_size: string;
    max_connected_machines?: (number | null);
    max_pod_machines?: (number | null);
    max_running?: (number | null);
    templates: Array<SizeOffer>;
};
