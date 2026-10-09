/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WidgetAnswer } from './WidgetAnswer.js';
export type WebWidgetResponse = {
    agent_id: string;
    allowed_origins: Array<string>;
    answer: WidgetAnswer;
    created_at: string;
    /**
     * The script tag that puts the chat on a page.
     */
    embed: string;
    id: string;
    looked_after_by: (string | null);
    name: string;
    /**
     * A page Lemma hosts with the chat on it, to share as a link.
     */
    page_url: string;
    public_key: string;
};
