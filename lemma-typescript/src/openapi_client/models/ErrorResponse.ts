/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Unified error envelope returned by every error response.
 *
 * All error responses (domain errors, HTTP exceptions, request validation
 * failures, and unexpected errors) share this shape. See
 * ``app.core.api.exception_handlers``.
 */
export type ErrorResponse = {
    code: string;
    details?: null;
    message: string;
    request_id?: (string | null);
};
