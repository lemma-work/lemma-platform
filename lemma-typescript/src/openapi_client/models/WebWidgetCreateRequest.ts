/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WidgetAnswer } from './WidgetAnswer.js';
import type { WidgetKind } from './WidgetKind.js';
export type WebWidgetCreateRequest = {
    /**
     * The agent that answers. The pod's assistant if omitted.
     */
    agent_name?: (string | null);
    allowed_origins?: Array<string>;
    answer?: WidgetAnswer;
    form_function?: (string | null);
    form_requires_code?: boolean;
    kind?: WidgetKind;
    looked_after_by?: (string | null);
    name: string;
};
