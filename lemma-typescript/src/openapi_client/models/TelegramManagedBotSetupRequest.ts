/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SurfaceBehaviorConfigInput } from './SurfaceBehaviorConfigInput.js';
export type TelegramManagedBotSetupRequest = {
    config?: SurfaceBehaviorConfigInput;
    default_agent_name?: (string | null);
    is_enabled?: boolean;
    /**
     * Pod-unique surface name. Defaults to telegram.
     */
    name?: (string | null);
};
