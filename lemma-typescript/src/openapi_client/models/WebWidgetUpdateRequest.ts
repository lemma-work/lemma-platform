/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FormRequest } from './FormRequest.js';
import type { WidgetAnswer } from './WidgetAnswer.js';
export type WebWidgetUpdateRequest = {
    allowed_origins?: (Array<string> | null);
    answer?: (WidgetAnswer | null);
    form?: (FormRequest | null);
    form_function?: (string | null);
    form_requires_code?: (boolean | null);
    looked_after_by?: (string | null);
};
