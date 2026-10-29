/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type OutcomeWeekResponse = {
    /**
     * When `value` was read; null when it never was.
     */
    measured_at?: (string | null);
    /**
     * Why its last reading could not be taken.
     */
    problem?: (string | null);
    /**
     * The Monday the week starts on (UTC).
     */
    starts_on: string;
    /**
     * The target when the week was read.
     */
    target?: (number | null);
    /**
     * The week's last reading. Null for a week it was not read in, or that answered null.
     */
    value?: (number | null);
};
