/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AutomationResponse } from './AutomationResponse.js';
import type { DayCountResponse } from './DayCountResponse.js';
import type { RecentChatResponse } from './RecentChatResponse.js';
export type PodRecentsResponse = {
    /**
     * One entry per day of the window, oldest first, zeros included.
     */
    activity: Array<DayCountResponse>;
    /**
     * Workflows and schedules that ran in the window, one each, the most recent first.
     */
    automations: Array<AutomationResponse>;
    /**
     * The member's latest chats, newest first.
     */
    chats: Array<RecentChatResponse>;
    pod_id: string;
    pod_name: string;
};
