/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodConnectorMode } from './PodConnectorMode.js';
import type { PodConnectorVisibility } from './PodConnectorVisibility.js';
export type PodConnectorCreateRequest = {
    /**
     * The organization installation this connector uses. Give this or `connector_id`.
     */
    auth_config_id?: (string | null);
    /**
     * A catalog connector (`gmail`) instead of an installation: its organization default, or -- when the organization has none and you may install connectors -- Lemma's own app for it, installed now.
     */
    connector_id?: (string | null);
    /**
     * PER_MEMBER (the default): each call uses the acting member's own account. SHARED: every call uses the one account set as `shared_pod_account_id`.
     */
    mode?: PodConnectorMode;
    /**
     * Shown to members when they are asked to connect.
     */
    purpose?: (string | null);
    /**
     * PER_MEMBER: ask every member who has no account to connect one.
     */
    required?: boolean;
    /**
     * SHARED only: a pod account its owner has already shared with the pod.
     */
    shared_pod_account_id?: (string | null);
    /**
     * Pod-local name apps, agents and bundles address it by, e.g. `gmail` or `support-inbox`. Left out: the installation's name, made free in this pod.
     */
    slug?: (string | null);
    visibility?: PodConnectorVisibility;
};
