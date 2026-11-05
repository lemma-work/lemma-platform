/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Absent is unchanged; neither field can be cleared.
 */
export type SourceUpdateRequest = {
    /**
     * Answer people outside the pod here, or stop.
     */
    answers_outsiders?: (boolean | null);
    /**
     * `me`: answer for its outsiders yourself from now on.
     */
    owner?: (string | null);
};
