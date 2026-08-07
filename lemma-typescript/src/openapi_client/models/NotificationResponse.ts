/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { NotificationDeliveryStatus } from './NotificationDeliveryStatus.js';
import type { NotificationOriginKind } from './NotificationOriginKind.js';
import type { NotificationStatus } from './NotificationStatus.js';
/**
 * One notification, shaped for the inbox that renders it.
 *
 * Carries enough to draw the row *and* decide what its action button does,
 * without a second request: ``awaiting_response`` says whether to draw one at
 * all, and ``responds_through_action`` says whether it opens a text box or the
 * real form described by ``action``.
 */
export type NotificationResponse = {
    action?: (Record<string, any> | null);
    actor_agent_id?: (string | null);
    actor_user_id?: (string | null);
    awaiting_response: boolean;
    body: string;
    created_at: string;
    delivered_at?: (string | null);
    delivery_conversation_id?: (string | null);
    delivery_platform?: (string | null);
    delivery_status: NotificationDeliveryStatus;
    expects_response: boolean;
    expires_at?: (string | null);
    id: string;
    origin_conversation_id?: (string | null);
    origin_id?: (string | null);
    origin_kind: NotificationOriginKind;
    pod_id: string;
    read_at?: (string | null);
    responded_at?: (string | null);
    responds_through_action: boolean;
    response_data?: (Record<string, any> | null);
    response_summary?: (string | null);
    status: NotificationStatus;
    title: string;
    undeliverable_reason?: (string | null);
};
