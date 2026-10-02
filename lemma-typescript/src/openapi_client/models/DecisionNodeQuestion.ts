/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DeciderDefinition } from './DeciderDefinition.js';
import type { ExpressionInputBinding } from './ExpressionInputBinding.js';
import type { LiteralInputBinding } from './LiteralInputBinding.js';
/**
 * A closed question the node asks when none of its rules matched.
 *
 * Answered by the decisions module -- the decider's own rules, then System
 * One, then the system model -- and recorded once per step, so a resumed or
 * retried step reads the answer it already has.
 */
export type DecisionNodeQuestion = {
    /**
     * Option key to the id of the node that answer goes to. An answer with no branch falls through to the default outgoing edge.
     */
    branches?: Record<string, string>;
    /**
     * A pod decider's name, or `system:<name>` for one that ships with Lemma. Leave out to ask `definition` inline.
     */
    decider?: (string | null);
    /**
     * A decider asked inline: exactly one `choice` question. Nothing is learned for it; name a pod decider for answers people can correct.
     */
    definition?: (DeciderDefinition | null);
    /**
     * What the decision is about: one binding, or named bindings that become an object. Only the decider's input view of it reaches an engine.
     */
    input: ((ExpressionInputBinding | LiteralInputBinding) | Record<string, (ExpressionInputBinding | LiteralInputBinding)>);
    /**
     * The node to go to when no rung could answer. Without it an open question takes its fallback option's branch, or falls through.
     */
    on_open?: (string | null);
    /**
     * The question to branch on. Leave out when the decider asks one.
     */
    question_key?: (string | null);
};
