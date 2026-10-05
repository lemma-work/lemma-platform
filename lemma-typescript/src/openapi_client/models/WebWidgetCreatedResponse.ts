/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WidgetAnswer } from './WidgetAnswer.js';
import type { WidgetKind } from './WidgetKind.js';
export type WebWidgetCreatedResponse = {
    agent_id: string;
    allowed_origins: Array<string>;
    answer: WidgetAnswer;
    created_at: string;
    /**
     * The script tag that puts the widget on a page.
     */
    embed: string;
    form_function: (string | null);
    form_requires_code: boolean;
    id: string;
    kind: WidgetKind;
    looked_after_by: (string | null);
    name: string;
    public_key: string;
    /**
     * Signs host tokens on the customer's server. Shown this once; keep it off web pages.
     */
    signing_secret: string;
};
