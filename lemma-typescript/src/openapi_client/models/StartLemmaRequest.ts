/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutcomeInput } from './OutcomeInput.js';
/**
 * Body for starting a Lemma: from a template, or from its own words.
 */
export type StartLemmaRequest = {
    /**
     * What it is for. A template's own when omitted.
     */
    description?: (string | null);
    /**
     * For a template that is a finish line: the date it is due by.
     */
    due?: (string | null);
    /**
     * Its short name: the template's when omitted, and required without one. A name already taken in the organization gets a number after it.
     */
    name?: (string | null);
    organization_id: string;
    /**
     * What it owns, for a Lemma started without a template. Without a measure: nothing it could count exists yet, so its agent sets one once it has built what it counts.
     */
    outcome?: (OutcomeInput | null);
    /**
     * A template from GET /pods/bundle/templates. It decides what the Lemma owns and brings the tables, skill and schedules that count it.
     */
    template?: (string | null);
};
