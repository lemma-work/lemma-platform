/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Answer } from './Answer.js';
import type { RungTrace } from './RungTrace.js';
export type DecisionResponse = {
    answered_at: (string | null);
    answered_by_user_id: (string | null);
    answers: Record<string, Answer>;
    created_at: string;
    decider_name: (string | null);
    decider_scope: string;
    decider_version: (number | null);
    evidence: (string | null);
    id: string;
    open: Array<string>;
    status: string;
    subject_key: (string | null);
    trace: Array<RungTrace>;
    visibility: string;
};
