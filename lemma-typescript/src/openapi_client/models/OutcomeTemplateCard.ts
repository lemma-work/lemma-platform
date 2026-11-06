/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OutcomeKind } from './OutcomeKind.js';
import type { TemplateOutcome } from './TemplateOutcome.js';
import type { TemplatePlan } from './TemplatePlan.js';
/**
 * One Lemma a person can start, as the shelf shows it.
 */
export type OutcomeTemplateCard = {
    brings?: Array<string>;
    description?: (string | null);
    /**
     * Which character's face it wears.
     */
    face?: (string | null);
    /**
     * The template's name, passed as `template` to start one.
     */
    id: string;
    kind: OutcomeKind;
    /**
     * The short name a Lemma made from it starts with.
     */
    name: string;
    /**
     * What it owns once started, with {due} filled in.
     */
    outcome: TemplateOutcome;
    /**
     * Where its work usually happens.
     */
    place?: (string | null);
    plan?: TemplatePlan;
    position?: number;
    /**
     * What it owns. A finish line's carries {due}.
     */
    sentence: string;
};
