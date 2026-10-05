/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FormFieldRequest } from './FormFieldRequest.js';
/**
 * A form on one table: the columns to ask for, in order.
 */
export type FormRequest = {
    confirmation?: (string | null);
    fields: Array<FormFieldRequest>;
    intro?: (string | null);
    table: string;
};
