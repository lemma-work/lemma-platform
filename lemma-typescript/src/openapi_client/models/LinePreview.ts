/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PersonRef } from './PersonRef.js';
export type LinePreview = {
    created_at: string;
    /**
     * Its writer deleted it.
     */
    deleted?: boolean;
    id: string;
    sender: PersonRef;
    seq: number;
    /**
     * The start of what was said; empty when withheld or deleted.
     */
    text: string;
    withheld?: boolean;
};
