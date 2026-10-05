/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FormRequest } from './FormRequest.js';
import type { WidgetAnswer } from './WidgetAnswer.js';
import type { WidgetKind } from './WidgetKind.js';
export type WebWidgetCreateRequest = {
    /**
     * The agent that answers. The pod's assistant if omitted.
     */
    agent_name?: (string | null);
    allowed_origins?: Array<string>;
    answer?: WidgetAnswer;
    /**
     * A form built from a table: submitting it adds one row. Takes the place of form_function.
     */
    form?: (FormRequest | null);
    form_function?: (string | null);
    form_requires_code?: boolean;
    kind?: WidgetKind;
    looked_after_by?: (string | null);
    name: string;
};
