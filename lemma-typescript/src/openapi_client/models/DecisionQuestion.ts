/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecisionQuestionExample } from './DecisionQuestionExample.js';
import type { ExpressionInputBinding } from './ExpressionInputBinding.js';
import type { JsonValue } from './JsonValue.js';
import type { LiteralInputBinding } from './LiteralInputBinding.js';
/**
 * One closed question, asked about one piece of evidence, routed on.
 */
export type DecisionQuestion = {
    /**
     * The question, as one closed JSON Schema property whose `description` is the question: a choice (`{"type": "string", "enum": [...]}` or `oneOf` of `{const, description}`), yes or no (`{"type": "boolean"}`), or a scale (`{"type": "integer", "minimum": 1, "maximum": 5}` or `oneOf` of integer levels). A multi-choice is not routable and is refused.
     */
    answer: Record<string, JsonValue>;
    /**
     * What to judge, as an input binding resolved against the run context -- an email, an event, a row. Never followed as instructions.
     */
    evidence: (ExpressionInputBinding | LiteralInputBinding);
    /**
     * Past cases and their answers, to steer the provider.
     */
    examples?: Array<DecisionQuestionExample>;
    /**
     * What to judge and how, in the author's words. Trusted: it is the only part of the question the provider follows.
     */
    instruction: string;
    /**
     * An answer whose confidence is below this counts as unsure. Ignored when the provider reports no confidence, as a language model does.
     */
    min_confidence?: (number | null);
    /**
     * Next node id by answer, the answer written as a string: `true` or `false` for yes or no, `3` for a scale level, the option itself for a choice. An answer with no route takes the node's default edge -- its first outgoing edge.
     */
    routes?: Record<string, string>;
    /**
     * Where to go when the evidence does not support an answer, or the answer's confidence is below `min_confidence`. Unset, an unsure answer takes the default edge.
     */
    unsure_next_node_id?: (string | null);
};
