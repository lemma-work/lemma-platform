/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ImpactedPod } from './ImpactedPod.js';
import type { ImpactedPodConnector } from './ImpactedPodConnector.js';
/**
 * What deleting or disabling an installation stops, before it is done.
 *
 * The counts are the whole organization's; `pods` and `pod_connectors` name
 * only the pods the caller can open, and the rest are counted in
 * `unnamed_pod_count`.
 */
export type InstallationImpactResponse = {
    account_count: number;
    auth_config_id: string;
    /**
     * False when the surfaces, schedules and subscriptions could not be counted just now: the counts above them are zero for that reason, not because nothing stops. Removing is not blocked by it.
     */
    complete?: boolean;
    /**
     * MCP event subscriptions held on its accounts.
     */
    event_subscription_count?: number;
    pod_account_count: number;
    /**
     * Its pod connectors in the pods the caller can open.
     */
    pod_connectors: Array<ImpactedPodConnector>;
    /**
     * Per pod the caller can open: what stops there.
     */
    pods?: Array<ImpactedPod>;
    /**
     * Schedules that listen through one of its pod connectors.
     */
    schedule_count?: number;
    /**
     * Surfaces that answer through one of its pod connectors.
     */
    surface_count?: number;
    /**
     * The pod connectors those unnamed pods lose.
     */
    unnamed_pod_connector_count?: number;
    /**
     * Pods that lose a connector and are not named here: the caller cannot open them, or (with `complete: false`) whether they can could not be checked.
     */
    unnamed_pod_count?: number;
};
