/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WidgetAnswer } from './WidgetAnswer.js';
export type WebWidgetCreateRequest = {
    /**
     * The agent that answers. The pod's assistant if omitted.
     */
    agent_name?: (string | null);
    allowed_origins?: Array<string>;
    answer?: WidgetAnswer;
    looked_after_by?: (string | null);
    name: string;
};
