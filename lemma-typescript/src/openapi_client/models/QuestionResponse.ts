/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PersonRef } from './PersonRef.js';
import type { QuestionKind } from './QuestionKind.js';
import type { QuestionState } from './QuestionState.js';
export type QuestionResponse = {
    answer?: (Record<string, any> | null);
    answered_at?: (string | null);
    answered_by?: (PersonRef | null);
    /**
     * The one person who may answer it.
     */
    awaits: PersonRef;
    expires_at?: (string | null);
    kind: QuestionKind;
    /**
     * What is asked, in the asker's shape: a `prompt`, and `choices` or a form `schema` where it offers them.
     */
    question: Record<string, any>;
    state: QuestionState;
};
