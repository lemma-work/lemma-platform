/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DataStoreWorkflowStartInput } from './DataStoreWorkflowStartInput.js';
import type { EventWorkflowStartInput } from './EventWorkflowStartInput.js';
import type { ManualWorkflowStartInput } from './ManualWorkflowStartInput.js';
import type { ResourceVisibility } from './ResourceVisibility.js';
import type { ScheduledWorkflowStartInput } from './ScheduledWorkflowStartInput.js';
import type { WorkflowMode } from './WorkflowMode.js';
export type WorkflowUpdateRequest = {
    /**
     * Updated workflow description.
     */
    description?: (string | null);
    /**
     * Updated public icon URL for the workflow.
     */
    icon_url?: (string | null);
    /**
     * Updated workflow schedule ownership mode.
     */
    mode?: (WorkflowMode | null);
    /**
     * Updated start trigger configuration.
     */
    start?: ((ManualWorkflowStartInput | ScheduledWorkflowStartInput | EventWorkflowStartInput | DataStoreWorkflowStartInput) | null);
    visibility?: (ResourceVisibility | null);
};
