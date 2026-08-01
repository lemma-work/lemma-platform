/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Deprecated. Use :class:`ConnectorKind`.
 *
 * Retained so callers outside this module keep working for one release. Read
 * paths map through :func:`kind_to_provider`; ``LEMMA`` means "any non-Composio
 * kind" and therefore cannot round-trip back to a single kind on its own.
 */
export enum AuthProvider {
    LEMMA = 'LEMMA',
    COMPOSIO = 'COMPOSIO',
}
