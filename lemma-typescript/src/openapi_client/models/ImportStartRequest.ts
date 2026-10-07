/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BundleSourceKind } from './BundleSourceKind.js';
/**
 * Body for starting an import.
 */
export type ImportStartRequest = {
    /**
     * Connector account for a private GitHub repo.
     */
    account_id?: (string | null);
    /**
     * URL (a lemma signed download URL), GITHUB (a public repo), or TEMPLATE (a template that ships with Lemma, by name).
     */
    kind: BundleSourceKind;
    /**
     * GITHUB repo owner.
     */
    owner?: (string | null);
    /**
     * GITHUB branch/tag/sha (optional).
     */
    ref?: (string | null);
    /**
     * GITHUB repo name.
     */
    repo?: (string | null);
    /**
     * For TEMPLATE: the template's name, e.g. 'support-desk'. Required with TEMPLATE and refused with any other kind.
     */
    template?: (string | null);
    /**
     * For URL: a lemma bundle download URL (from an export or an upload). For GITHUB: the repo URL (alternative to owner+repo).
     */
    url?: (string | null);
};
