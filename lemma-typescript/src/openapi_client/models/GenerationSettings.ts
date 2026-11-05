/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ReasoningEffort } from './ReasoningEffort.js';
/**
 * Portable generation settings. Every field unset means "provider default".
 *
 * Layered lowest to highest: the model's catalog entry, the runtime profile,
 * the agent, the workflow step. A field a layer leaves unset is inherited
 * from the layer below, so an agent that only lowers reasoning effort keeps
 * whatever output cap its profile chose.
 */
export type GenerationSettings = {
    /**
     * Most tokens one model response may use, thinking included. Still clamped to the room the context window leaves.
     */
    max_output_tokens?: (number | null);
    /**
     * Sent only to OpenAI-compatible models whose catalog entry declares the REASONING capability; ignored elsewhere.
     */
    reasoning_effort?: (ReasoningEffort | null);
    temperature?: (number | null);
    /**
     * Extended-thinking budget. Sent only to Anthropic-compatible models whose catalog entry declares the REASONING capability; ignored elsewhere.
     */
    thinking_budget_tokens?: (number | null);
    top_p?: (number | null);
};
