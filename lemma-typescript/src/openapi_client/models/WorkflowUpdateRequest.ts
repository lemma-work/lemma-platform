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
     * How many runs of this workflow may be in progress at once. A run started past the limit is not refused: it waits, with a `QUEUED` active wait, and starts when an earlier run finishes. Null means no limit. Send null to remove it.
     */
    max_concurrent_runs?: (number | null);
    /**
     * Updated workflow schedule ownership mode.
     */
    mode?: (WorkflowMode | null);
    /**
     * What each run is about, as up to four JMESPath expressions over the run context, joined with ' · '. Example: `["collect.candidate_name", "collect.role"]`. Evaluated whenever a run is read, so a part filled in by a later form appears once that form is answered; parts that resolve to nothing are skipped. Empty means runs carry no title. Send `[]` to remove the title.
     */
    run_title?: (Array<string> | null);
    /**
     * Updated start trigger configuration.
     */
    start?: ((ManualWorkflowStartInput | ScheduledWorkflowStartInput | EventWorkflowStartInput | DataStoreWorkflowStartInput) | null);
    visibility?: (ResourceVisibility | null);
};
