/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SourceKind } from './SourceKind.js';
export type ChannelSourceResponse = {
    id: string;
    /**
     * The platform keeps it to its members.
     */
    is_private: boolean;
    kind: SourceKind;
    last_inbound_at?: (string | null);
    /**
     * A group the bot asked the platform for, not confirmed yet.
     */
    pending?: boolean;
    /**
     * Where it is: TELEGRAM, WHATSAPP, SLACK, TEAMS, RESEND, WEB...
     */
    platform: string;
    surface_id?: (string | null);
    title?: (string | null);
};
