/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { StartedPodResponse } from './StartedPodResponse.js';
export type StartLemmaResponse = {
    /**
     * The chat where its agent sets itself up, opened with the person's first message. From a template, its first turn starts once the template is applied.
     */
    conversation_id: string;
    /**
     * The template's import, readable at GET /pods/{pod_id}/bundle/imports/{import_id} while it is applied; null without a template.
     */
    import_id?: (string | null);
    pod: StartedPodResponse;
};
