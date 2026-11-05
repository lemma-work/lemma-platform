/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DeliveryStatus } from './DeliveryStatus.js';
/**
 * Where a line went outside Lemma, as everybody who reads it is shown.
 */
export type LineDelivery = {
    /**
     * The delivery; retry it by this id.
     */
    id: string;
    platform?: (string | null);
    /**
     * Why it was skipped, failed or given up.
     */
    reason?: (string | null);
    source_id?: (string | null);
    status: DeliveryStatus;
};
