/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { EventWorkflowStartConfigOutput } from './EventWorkflowStartConfigOutput.js';
export type EventWorkflowStartOutput = {
    /**
     * Connector trigger configuration for this workflow.
     */
    config: EventWorkflowStartConfigOutput;
    /**
     * Event-triggered workflow start.
     */
    type?: string;
};
