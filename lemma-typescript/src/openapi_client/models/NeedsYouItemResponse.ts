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
 * workflow run for FORM and DECISION; a contact's channel for CONTACT; the
 * member's own line for MESSAGE, with the line in ``message_id``.
 */
export type NeedsYouItemResponse = {
    channel_id?: (string | null);
    /**
     * MESSAGE: the answers it offered, each sent as `{choice}`. An answer in words, `{text}`, is always allowed too.
     */
    choices?: Array<string>;
    conversation_id?: (string | null);
    /**
     * Where it is. The conversation's title when `title` is the question; the workflow's name; or why the contact asked for a person. MESSAGE: the whole question, when `title` had to cut it.
     */
    detail?: (string | null);
    /**
     * Stable across reads: `conversation:<id>`, `wait:<id>`, `contact:<channel id>` or `message:<line id>`.
     */
    key: string;
    kind: NeedsYouKind;
    /**
     * MESSAGE: the line it was asked on. Answer it with `POST /pods/{pod_id}/channels/{channel_id}/messages/{message_id}/answer`.
     */
    message_id?: (string | null);
    pod_id: string;
    pod_name: string;
    /**
     * When it started waiting.
     */
    since: string;
    /**
     * What is asked. QUESTION/APPROVAL/PERMISSION: the question or what the agent wants to do, else the conversation's title. FORM/DECISION: the decision's question or the form's title, else the workflow's name. CONTACT: the contact's name. MESSAGE: the first line of what the agent asked.
     */
    title?: (string | null);
    /**
     * A conversation's pause, as the approval id its decision endpoint takes.
     */
    tool_call_id?: (string | null);
    workflow_run_id?: (string | null);
};
