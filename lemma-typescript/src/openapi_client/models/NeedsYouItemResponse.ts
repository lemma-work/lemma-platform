/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NeedsYouKind } from './NeedsYouKind.js';
/**
 * One thing waiting on the member, and where to answer it.
 *
 * Exactly one of ``conversation_id``, ``workflow_run_id`` and ``channel_id``
 * is set, by kind: a conversation for QUESTION, APPROVAL and PERMISSION; a
 * workflow run for FORM and DECISION; a contact's channel for CONTACT.
 */
export type NeedsYouItemResponse = {
    channel_id?: (string | null);
    conversation_id?: (string | null);
    /**
     * Where it is. The conversation's title when `title` is the question; the workflow's name; or why the contact asked for a person.
     */
    detail?: (string | null);
    /**
     * Stable across reads: `conversation:<id>`, `wait:<id>` or `contact:<channel id>`.
     */
    key: string;
    kind: NeedsYouKind;
    pod_id: string;
    pod_name: string;
    /**
     * When it started waiting.
     */
    since: string;
    /**
     * What is asked. QUESTION/APPROVAL/PERMISSION: the question or what the agent wants to do, else the conversation's title. FORM/DECISION: the decision's question or the form's title, else the workflow's name. CONTACT: the contact's name.
     */
    title?: (string | null);
    /**
     * A conversation's pause, as the approval id its decision endpoint takes.
     */
    tool_call_id?: (string | null);
    workflow_run_id?: (string | null);
};
