/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FieldInput } from './FieldInput.js';
export type FormField = {
    column: string;
    column_type: string;
    hint?: (string | null);
    input: FieldInput;
    label: string;
    options?: Array<string>;
    required?: boolean;
};
