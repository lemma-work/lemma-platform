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
    /**
     * Whom it answers. Off until a member chooses.
     */
    answer?: WidgetAnswer;
    /**
     * The product whose users the widget's host tokens name, such as `acme-app`: give two widgets of one product the same issuer, and widgets of two products different ones, so their user 42s are two people. Letters, digits, dots, dashes and underscores. Fixed once the widget is made; `default` if omitted.
     */
    host_issuer?: (string | null);
    looked_after_by?: (string | null);
    name: string;
};
