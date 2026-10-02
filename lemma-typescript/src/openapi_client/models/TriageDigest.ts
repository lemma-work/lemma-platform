/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * When held events go out together.
 */
export type TriageDigest = {
    /**
     * Five-field cron for the digest, e.g. '0 9 * * 1-5'. No more often than a TIME schedule may fire.
     */
    cron: string;
    /**
     * IANA zone the cron is read in, e.g. 'Europe/Berlin'. Omitted means UTC.
     */
    timezone?: (string | null);
};
