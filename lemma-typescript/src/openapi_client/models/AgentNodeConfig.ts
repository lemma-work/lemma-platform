/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExpressionInputBinding } from './ExpressionInputBinding.js';
import type { GenerationSettings } from './GenerationSettings.js';
import type { LiteralInputBinding } from './LiteralInputBinding.js';
/**
 * Configuration for Agent node.
 */
export type AgentNodeConfig = {
    /**
     * Agent resource name to execute.
     */
    agent_name: string;
    /**
     * How the model generates for this step: reasoning effort, output cap, temperature. Each field set here replaces the agent's; each left unset keeps it.
     */
    generation?: (GenerationSettings | null);
    /**
     * Explicit mapping from agent input key to either an expression or a literal JSON value. Strings are never auto-interpreted.
     */
    input_mapping?: Record<string, (ExpressionInputBinding | LiteralInputBinding)>;
    /**
     * What this step is for. Added to the agent's own instructions for this run only; the agent keeps its identity and gains a task.
     */
    instructions?: (string | null);
    /**
     * Another model from the agent's runtime profile, for this step only. Unset runs on the agent's model.
     */
    model_name?: (string | null);
    /**
     * How long the step may run before the workflow stops it, in place of the deployment's ceiling. Checked by a periodic sweep, so the step ends within minutes of it, not to the second. A step waiting on a person gets the usual multiple of it.
     */
    timeout_seconds?: (number | null);
};
