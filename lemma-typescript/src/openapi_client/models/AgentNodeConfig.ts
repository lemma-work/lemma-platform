/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExpressionInputBinding } from './ExpressionInputBinding.js';
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
     * Explicit mapping from agent input key to either an expression or a literal JSON value. Strings are never auto-interpreted.
     */
    input_mapping?: Record<string, (ExpressionInputBinding | LiteralInputBinding)>;
};
