/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentNodeResponse } from './AgentNodeResponse.js';
import type { DataStoreWorkflowStartOutput } from './DataStoreWorkflowStartOutput.js';
import type { DecisionNodeResponse } from './DecisionNodeResponse.js';
import type { EndNodeResponse } from './EndNodeResponse.js';
import type { EventWorkflowStartOutput } from './EventWorkflowStartOutput.js';
import type { FormNodeResponse } from './FormNodeResponse.js';
import type { FunctionNodeResponse } from './FunctionNodeResponse.js';
import type { LoopNodeResponse } from './LoopNodeResponse.js';
import type { ManualWorkflowStartOutput } from './ManualWorkflowStartOutput.js';
import type { ScheduledWorkflowStartOutput } from './ScheduledWorkflowStartOutput.js';
import type { WaitUntilNodeResponse } from './WaitUntilNodeResponse.js';
import type { WorkflowEdge } from './WorkflowEdge.js';
import type { WorkflowMode } from './WorkflowMode.js';
export type WorkflowDetailResponse = {
    allowed_actions?: Array<string>;
    created_at?: (string | null);
    description?: (string | null);
    edges?: Array<WorkflowEdge>;
    icon_url?: (string | null);
    id: string;
    is_active?: boolean;
    mode?: WorkflowMode;
    name: string;
    nodes?: Array<(FormNodeResponse | AgentNodeResponse | FunctionNodeResponse | DecisionNodeResponse | LoopNodeResponse | WaitUntilNodeResponse | EndNodeResponse)>;
    pod_id: string;
    /**
     * What each run is about, as up to four JMESPath expressions over the run context, joined with ' · '. Example: `["collect.candidate_name", "collect.role"]`. Evaluated whenever a run is read, so a part filled in by a later form appears once that form is answered; parts that resolve to nothing are skipped. Empty means runs carry no title.
     */
    run_title?: Array<string>;
    start?: ((ManualWorkflowStartOutput | ScheduledWorkflowStartOutput | EventWorkflowStartOutput | DataStoreWorkflowStartOutput) | null);
    updated_at?: (string | null);
    visibility?: string;
};
