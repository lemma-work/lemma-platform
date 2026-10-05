/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FieldInput } from './FieldInput.js';
export type FormColumnResponse = {
    description: (string | null);
    inputs: Array<FieldInput>;
    name: string;
    options: Array<string>;
    required: boolean;
    suggested_input: FieldInput;
    type: string;
};
