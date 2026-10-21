/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DeliveryStatus } from './DeliveryStatus.js';
import type { DeliveryTarget } from './DeliveryTarget.js';
export type DeliveryResponse = {
    attempts?: number;
    created_at?: (string | null);
    id: string;
    message_id: string;
    next_attempt_at?: (string | null);
    platform?: (string | null);
    reason?: (string | null);
    sent_at?: (string | null);
    status: DeliveryStatus;
    target: DeliveryTarget;
};
