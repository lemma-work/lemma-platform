/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutcomeKind } from './OutcomeKind.js';
/**
 * What a template's Lemma owns, before its date is known: a finish
 * line's sentence still carries ``{due}``.
 */
export type TemplateOutcome = {
    kind: OutcomeKind;
    /**
     * Counts rows of the tables the template brings.
     */
    measure?: (string | null);
    sentence: string;
    target?: (number | null);
    unit?: (string | null);
};
