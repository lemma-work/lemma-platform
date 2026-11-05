/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * How long a reasoning model thinks before it answers.
 *
 * The values are the OpenAI Chat Completions ``reasoning_effort`` vocabulary,
 * which is the wire format OPENAI_COMPATIBLE speaks. Which values a given
 * model accepts is the model's business; ``none`` and ``minimal`` in
 * particular are not universal.
 */
export enum ReasoningEffort {
    NONE = 'none',
    MINIMAL = 'minimal',
    LOW = 'low',
    MEDIUM = 'medium',
    HIGH = 'high',
}
