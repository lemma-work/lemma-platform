/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DecisionExampleBody } from './DecisionExampleBody.js';
import type { JsonValue } from './JsonValue.js';
export type MakeDecisionRequest = {
    /**
     * What to judge: an email, an event, a row, as text or JSON. Never followed as instructions. At most 64 KiB; send the part that matters.
     */
    evidence: JsonValue;
    /**
     * Past cases and their answers, to steer the provider.
     */
    examples?: Array<DecisionExampleBody>;
    /**
     * What to judge and how, in your words. Trusted: it is the only part of the request the provider follows.
     */
    instruction: string;
    /**
     * `interactive` when someone is waiting on the answer: a shorter deadline and no second attempt. `background` otherwise.
     */
    priority?: MakeDecisionRequest.priority;
    /**
     * The questions, as a flat JSON Schema object: one property per question, its `description` the question. Each property is a choice (`{"type": "string", "enum": [...]}`, or `oneOf` of `{const, description}`), a multi-choice (`{"type": "array", "items": <choice>, "uniqueItems": true}`), yes or no (`{"type": "boolean"}`), or a scale (`{"type": "integer", "minimum": 1, "maximum": 5}`, or `oneOf` of described integer levels). Free text and open-ended numbers are not supported.
     */
    schema: Record<string, JsonValue>;
};
export namespace MakeDecisionRequest {
    /**
     * `interactive` when someone is waiting on the answer: a shorter deadline and no second attempt. `background` otherwise.
     */
    export enum priority {
        INTERACTIVE = 'interactive',
        BACKGROUND = 'background',
    }
}
