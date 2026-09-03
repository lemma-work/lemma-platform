/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Schema for updating a record.
 */
export type UpdateRecordRequest = {
    /**
     * Partial record patch keyed by table column names.
     */
    data: Record<string, any>;
    /**
     * Optional optimistic-concurrency guard: the `updated_at` value the caller last read. The patch applies only while the row still carries it, and answers 409 when it does not — so two clients editing the same field cannot silently keep the later one. Omit it for last-writer-wins.
     */
    expected_updated_at?: (string | null);
};
