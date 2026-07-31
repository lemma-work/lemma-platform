/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MessageKind } from './MessageKind.js';
export type MessageResponse = {
    conversation_id: string;
    created_at: string;
    id: string;
    kind: MessageKind;
    metadata?: (Record<string, any> | null);
    role: string;
    sequence: number;
    text?: (string | null);
    tool_args?: null;
    tool_call_id?: (string | null);
    tool_name?: (string | null);
    tool_result?: null;
};
