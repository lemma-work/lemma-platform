/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentHostEventType } from './AgentHostEventType.js';
/**
 * One run event on its way to the run's Redis Stream.
 *
 * There is no event id: events are deduplicated by ``sequence`` against the
 * stream's watermark, which is what a resend after a Redis flush relies on.
 * There is no host timestamp either -- a Redis stream id already embeds the
 * millisecond it was appended.
 */
export type AgentHostEvent = {
    lease_epoch: number;
    object_id?: (string | null);
    payload?: Record<string, any>;
    run_id: string;
    sequence: number;
    type: AgentHostEventType;
};
