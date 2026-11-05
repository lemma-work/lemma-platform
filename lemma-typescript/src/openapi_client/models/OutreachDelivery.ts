/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DeliveryStatus } from './DeliveryStatus.js';
export type OutreachDelivery = {
    id: string;
    platform?: (string | null);
    reason?: (string | null);
    source_id?: (string | null);
    /**
     * PENDING until the line is sent; SENT, SKIPPED or FAILED after.
     */
    status: DeliveryStatus;
};
