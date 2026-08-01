/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NotificationOrigin } from './NotificationOrigin.js';
export type NotificationResponse = {
    agent_id?: (string | null);
    body: string;
    conversation_id?: (string | null);
    created_at: string;
    id: string;
    origin_id?: (string | null);
    origin_type?: (NotificationOrigin | null);
    pod_id: string;
    read_at?: (string | null);
    title?: (string | null);
};
