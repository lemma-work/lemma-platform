/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type DayCountResponse = {
    /**
     * Conversations last active that day that nobody typed the first message of: a schedule, a workflow, a notification, a task, or another pod's ask.
     */
    by_agent: number;
    /**
     * Conversations a person started.
     */
    by_people: number;
    /**
     * A calendar day in the requested time zone.
     */
    day: string;
};
