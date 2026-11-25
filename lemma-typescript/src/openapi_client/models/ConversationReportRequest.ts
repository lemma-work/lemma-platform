/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ReportReason } from './ReportReason.js';
/**
 * Why the reader reports this message, and anything they want to add.
 */
export type ConversationReportRequest = {
    /**
     * The reader's own words, for the team. Optional.
     */
    note?: (string | null);
    /**
     * `OFFENSIVE` offensive or hateful, `HARMFUL` harmful or dangerous, `SEXUAL` sexual, `SPAM` spam or a scam, `OTHER` something else.
     */
    reason: ReportReason;
};
