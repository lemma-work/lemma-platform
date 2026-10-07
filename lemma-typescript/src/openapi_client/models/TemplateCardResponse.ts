/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TemplateOfferResponse } from './TemplateOfferResponse.js';
import type { TemplateSkillResponse } from './TemplateSkillResponse.js';
import type { TemplateTableResponse } from './TemplateTableResponse.js';
import type { TemplateWinResponse } from './TemplateWinResponse.js';
/**
 * A role on the hiring shelf, read from the template that makes it.
 */
export type TemplateCardResponse = {
    /**
     * The template's description; written onto the pod.
     */
    about: string;
    brings: Array<string>;
    /**
     * The measures its scorecard turns on, beyond the ones every teammate has.
     */
    judged_on: Array<string>;
    name: string;
    offers: Array<TemplateOfferResponse>;
    /**
     * The job, in one line.
     */
    role: string;
    /**
     * The archetype face's seed.
     */
    seed: string;
    skills: Array<TemplateSkillResponse>;
    tables: Array<TemplateTableResponse>;
    /**
     * Pass as `template` to a kind=TEMPLATE import.
     */
    template: string;
    wins: Array<TemplateWinResponse>;
};
