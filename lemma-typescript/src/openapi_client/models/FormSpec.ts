/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FormField } from './FormField.js';
/**
 * What a form asks for, and what it says before and after.
 */
export type FormSpec = {
    confirmation?: (string | null);
    fields: Array<FormField>;
    intro?: (string | null);
    table: string;
};
