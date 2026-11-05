/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ConnectorChoice } from './ConnectorChoice.js';
/**
 * Body for applying a planned import.
 */
export type ApplyImportRequest = {
    /**
     * Required to proceed when the plan has destructive steps.
     */
    confirm_destructive?: boolean;
    /**
     * Choices for the plan's `connectors`, by slug: which installation (default: the plan's `auth_config_id`), or to create one, and for a SHARED connector which of your own accounts to share. Account ids are never in a bundle; they are chosen here, in your own org.
     */
    connectors?: Record<string, ConnectorChoice>;
    /**
     * Resolved values for the plan's ${var} placeholders.
     */
    variables?: Record<string, string>;
};
