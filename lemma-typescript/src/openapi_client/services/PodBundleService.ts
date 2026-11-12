/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApplyImportRequest } from '../models/ApplyImportRequest.js';
import type { ExportStartRequest } from '../models/ExportStartRequest.js';
import type { ExportStatusResponse } from '../models/ExportStatusResponse.js';
import type { fastapi___compat__v2__Body_pod__bundle__upload } from '../models/fastapi___compat__v2__Body_pod__bundle__upload.js';
import type { ImportStartRequest } from '../models/ImportStartRequest.js';
import type { ImportStatusResponse } from '../models/ImportStatusResponse.js';
import type { OutcomeTemplateCard } from '../models/OutcomeTemplateCard.js';
import type { PublishStartRequest } from '../models/PublishStartRequest.js';
import type { PublishStatusResponse } from '../models/PublishStatusResponse.js';
import type { StartLemmaRequest } from '../models/StartLemmaRequest.js';
import type { StartLemmaResponse } from '../models/StartLemmaResponse.js';
import type { UploadResponse } from '../models/UploadResponse.js';
import type { CancelablePromise } from '../core/CancelablePromise.js';
import { OpenAPI } from '../core/OpenAPI.js';
import { request as __request } from '../core/request.js';
export class PodBundleService {
    /**
     * Download A Bundle Archive
     * Stream a bundle archive (application/zip) by signed token. Requires an authenticated lemma user AND a valid token; not pod-scoped, so a share link works for any signed-in user. 410 if the token is invalid/expired or the archive was swept.
     * @param token Signed download token.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static podBundleDownload(
        token: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/bundle/download',
            query: {
                'token': token,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Templates to start a Lemma from
     * The templates that ship with this server, one card each: what a Lemma made from it owns and how that is counted, its face, where its work happens, what it brings and the first lines of its plan. Start one with POST /pods/start.
     * @returns OutcomeTemplateCard Successful Response
     * @throws ApiError
     */
    public static podBundleTemplates(): CancelablePromise<Array<OutcomeTemplateCard>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/bundle/templates',
        });
    }
    /**
     * Start a Lemma
     * Make a pod that owns something, in one transaction: the pod, what it owns, a first /plan.md, and the chat where its agent sets itself up, opened with the caller's first message. From a template (`template`, with `due` for a finish line) it also brings the template's tables, skill and schedules, applied by a worker, and its agent starts once they are in; from the caller's own words (`outcome` and `name`) its agent starts at once. A failure leaves nothing behind. 404 for a template that is not here; 403 outside the organization; 409 when its name collides with one made at the same moment.
     * @param requestBody
     * @returns StartLemmaResponse Successful Response
     * @throws ApiError
     */
    public static podStart(
        requestBody: StartLemmaRequest,
    ): CancelablePromise<StartLemmaResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/start',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Start Pod Export
     * Enqueue a pod export. Returns 202 with an export_id; poll the status endpoint until READY, then fetch the signed download_url.
     * @param podId
     * @param requestBody
     * @returns ExportStatusResponse Successful Response
     * @throws ApiError
     */
    public static podBundleExportStart(
        podId: string,
        requestBody: ExportStartRequest,
    ): CancelablePromise<ExportStatusResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/bundle/exports',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Pod Export Status
     * Poll the status of a pod export (Redis-only; 410 when expired). When READY, includes the signed download_url, its expires_at, and any data-cap warnings.
     * @param podId
     * @param exportId
     * @returns ExportStatusResponse Successful Response
     * @throws ApiError
     */
    public static podBundleExportGet(
        podId: string,
        exportId: string,
    ): CancelablePromise<ExportStatusResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/bundle/exports/{export_id}',
            path: {
                'pod_id': podId,
                'export_id': exportId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Start Pod Import
     * Import a pod bundle from a URL. kind=URL takes a lemma signed download URL (from an export, or from POST …/bundle/uploads); kind=GITHUB takes a repo (repo_url or owner+repo), fetched through the pod's `github` connector as the importer, or anonymously for a public repo; kind=TEMPLATE takes the name of a template that ships with Lemma (404 when there is none by that name). Returns 202 with an import_id; poll status until AWAITING_CONFIRMATION, review the plan, then apply.
     * @param podId
     * @param requestBody
     * @returns ImportStatusResponse Successful Response
     * @throws ApiError
     */
    public static podBundleImportStart(
        podId: string,
        requestBody: ImportStartRequest,
    ): CancelablePromise<ImportStatusResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/bundle/imports',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Cancel Pod Import
     * Abort a running import and delete its state + staged archive.
     * @param podId
     * @param importId
     * @returns ImportStatusResponse Successful Response
     * @throws ApiError
     */
    public static podBundleImportCancel(
        podId: string,
        importId: string,
    ): CancelablePromise<ImportStatusResponse> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/pods/{pod_id}/bundle/imports/{import_id}',
            path: {
                'pod_id': podId,
                'import_id': importId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Pod Import Status
     * Poll the status + plan of a pod import (Redis-only; 410 when expired).
     * @param podId
     * @param importId
     * @returns ImportStatusResponse Successful Response
     * @throws ApiError
     */
    public static podBundleImportGet(
        podId: string,
        importId: string,
    ): CancelablePromise<ImportStatusResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/bundle/imports/{import_id}',
            path: {
                'pod_id': podId,
                'import_id': importId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Apply Pod Import
     * Apply a planned import. Requires confirm_destructive when the plan drops or alters columns, and resolved values for any required variables. Returns 202; poll the status endpoint for per-step progress.
     * @param podId
     * @param importId
     * @param requestBody
     * @returns ImportStatusResponse Successful Response
     * @throws ApiError
     */
    public static podBundleImportApply(
        podId: string,
        importId: string,
        requestBody: ApplyImportRequest,
    ): CancelablePromise<ImportStatusResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/bundle/imports/{import_id}/apply',
            path: {
                'pod_id': podId,
                'import_id': importId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Stream Pod Import Progress
     * Server-Sent Events for an import. The first frame is a full state snapshot; subsequent frames are live status/step/progress updates. The stream closes when the import reaches a terminal state or expires.
     * @param podId
     * @param importId
     * @returns any Successful Response
     * @throws ApiError
     */
    public static podBundleImportEvents(
        podId: string,
        importId: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/bundle/imports/{import_id}/events',
            path: {
                'pod_id': podId,
                'import_id': importId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Re-plan Pod Import
     * Re-run planning against the still-staged bundle (410 if swept).
     * @param podId
     * @param importId
     * @returns ImportStatusResponse Successful Response
     * @throws ApiError
     */
    public static podBundleImportReplan(
        podId: string,
        importId: string,
    ): CancelablePromise<ImportStatusResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/bundle/imports/{import_id}/replan',
            path: {
                'pod_id': podId,
                'import_id': importId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Publish Pod To GitHub
     * Publish the pod as a bundle to a GitHub repository, as the account the pod's `github` connector resolves to for the caller. Refused up front with `CONNECTION_REQUIRED` / `REAUTH_REQUIRED` (with `connect_url`) or `POD_CONNECTOR_NOT_FOUND` when there is none. Returns 202 with a publish_id; poll the status endpoint for the repo URL.
     * @param podId
     * @param requestBody
     * @returns PublishStatusResponse Successful Response
     * @throws ApiError
     */
    public static podBundlePublishStart(
        podId: string,
        requestBody: PublishStartRequest,
    ): CancelablePromise<PublishStatusResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/bundle/publishes',
            path: {
                'pod_id': podId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Pod Publish Status
     * Poll the status of a pod publish (Redis-only; 410 when expired).
     * @param podId
     * @param publishId
     * @returns PublishStatusResponse Successful Response
     * @throws ApiError
     */
    public static podBundlePublishGet(
        podId: string,
        publishId: string,
    ): CancelablePromise<PublishStatusResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/bundle/publishes/{publish_id}',
            path: {
                'pod_id': podId,
                'publish_id': publishId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Stream Pod Publish Progress
     * Server-Sent Events for a publish (snapshot then live frames).
     * @param podId
     * @param publishId
     * @returns any Successful Response
     * @throws ApiError
     */
    public static podBundlePublishEvents(
        podId: string,
        publishId: string,
    ): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/pods/{pod_id}/bundle/publishes/{publish_id}/events',
            path: {
                'pod_id': podId,
                'publish_id': publishId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Stage A Local Bundle Upload
     * Upload a local .zip bundle and receive a signed lemma download URL to pass to POST …/bundle/imports as kind=URL. The only multipart endpoint; it stages bytes and mints a URL, nothing more.
     * @param podId
     * @param formData
     * @returns UploadResponse Successful Response
     * @throws ApiError
     */
    public static podBundleUpload(
        podId: string,
        formData: fastapi___compat__v2__Body_pod__bundle__upload,
    ): CancelablePromise<UploadResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/pods/{pod_id}/bundle/uploads',
            path: {
                'pod_id': podId,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
