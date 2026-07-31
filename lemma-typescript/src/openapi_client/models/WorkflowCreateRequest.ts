/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentNode } from './AgentNode.js';
import type { DataStoreWorkflowStartInput } from './DataStoreWorkflowStartInput.js';
import type { DecisionNode } from './DecisionNode.js';
import type { EndNode } from './EndNode.js';
import type { EventWorkflowStartInput } from './EventWorkflowStartInput.js';
import type { FormNode } from './FormNode.js';
import type { FunctionNode } from './FunctionNode.js';
import type { LoopNode } from './LoopNode.js';
import type { ManualWorkflowStartInput } from './ManualWorkflowStartInput.js';
import type { ResourceVisibility } from './ResourceVisibility.js';
import type { ScheduledWorkflowStartInput } from './ScheduledWorkflowStartInput.js';
import type { WaitUntilNode } from './WaitUntilNode.js';
import type { WorkflowEdge } from './WorkflowEdge.js';
import type { WorkflowMode } from './WorkflowMode.js';
export type WorkflowCreateRequest = {
    /**
     * Optional workflow description.
     */
    description?: (string | null);
    /**
     * Optional initial graph edges connecting the provided nodes.
     */
    edges?: Array<WorkflowEdge>;
    /**
     * Optional public icon URL for the workflow.
     */
    icon_url?: (string | null);
    /**
     * Workflow schedule ownership mode. `GLOBAL` means one pod-level workflow schedule is allowed; `USER` is reserved for per-user schedule ownership.
     */
    mode?: WorkflowMode;
    /**
     * Workflow name.
     */
    name: string;
    /**
     * Optional initial graph nodes. When provided, the graph is stored at creation time so a separate `workflow.graph.update` call is not required. Omit (or pass an empty list) to create a shell and upload the graph later. Node `input_mapping` entries must use explicit typed bindings like `{"type": "expression", "value": "start.payload.x"}`.
     */
    nodes?: Array<(FormNode | AgentNode | FunctionNode | DecisionNode | LoopNode | WaitUntilNode | EndNode)>;
    /**
     * Start configuration. If omitted, the workflow can be started manually via `workflow.start`.
     */
    start?: ((ManualWorkflowStartInput | ScheduledWorkflowStartInput | EventWorkflowStartInput | DataStoreWorkflowStartInput) | null);
    visibility?: ResourceVisibility;
};
