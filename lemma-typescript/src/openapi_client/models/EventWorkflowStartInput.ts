/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EventWorkflowStartConfigInput } from './EventWorkflowStartConfigInput.js';
export type EventWorkflowStartInput = {
    /**
     * Connector trigger configuration for this workflow.
     */
    config: EventWorkflowStartConfigInput;
    /**
     * Event-triggered workflow start.
     */
    type?: string;
};
