/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Where an imported bundle comes from — a CAPS wire enum.
 *
 * ``URL`` covers any lemma-origin signed download URL (an export or an
 * uploaded ``.zip`` staged into our object storage); ``GITHUB`` is a public
 * repo fetched via the connector path; ``TEMPLATE`` is a bundle that ships
 * with the backend under ``pod_bundle/templates/``, named rather than located.
 */
export enum BundleSourceKind {
    URL = 'URL',
    GITHUB = 'GITHUB',
    TEMPLATE = 'TEMPLATE',
}
