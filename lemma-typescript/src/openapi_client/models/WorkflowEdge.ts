/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkflowEdgeKind } from './WorkflowEdgeKind.js';
/**
 * An edge in the workflow graph.
 */
export type WorkflowEdge = {
    id: string;
    /**
     * `NEXT` is followed when the source finishes. `FAILURE` is followed instead of failing the run when the source fails: its output is then `{error}` (with any partial output), read as `<source>.error`. At most one FAILURE edge per node, never from an END. A LOOP's also catches failures in its body that no body node routes itself.
     */
    kind?: WorkflowEdgeKind;
    label?: (string | null);
    source: string;
    target: string;
};
