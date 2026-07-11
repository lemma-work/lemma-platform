/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BundleSourceKind } from './BundleSourceKind.js';
/**
 * Body for starting a URL-based import.
 */
export type ImportStartRequest = {
    /**
     * Connector account for a private GitHub repo.
     */
    account_id?: (string | null);
    /**
     * URL (a lemma signed download URL) or GITHUB (a public repo).
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
     * For URL: a lemma bundle download URL (from an export or an upload). For GITHUB: the repo URL (alternative to owner+repo).
     */
    url?: (string | null);
};
