/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FormNodeConfig } from './FormNodeConfig.js';
/**
 * Form node for user input. The run waits on it until the form is
 * submitted via the form-submit endpoint.
 */
export type FormNode = {
    config: FormNodeConfig;
    id: string;
    label?: (string | null);
    position?: (Record<string, number> | null);
    type?: string;
};
