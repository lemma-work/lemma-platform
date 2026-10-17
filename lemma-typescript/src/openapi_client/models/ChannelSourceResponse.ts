/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SourceKind } from './SourceKind.js';
export type ChannelSourceResponse = {
    id: string;
    kind: SourceKind;
    last_inbound_at?: (string | null);
    /**
     * Where it is: TELEGRAM, WHATSAPP, SLACK, RESEND, WEB…
     */
    platform: string;
    surface_id?: (string | null);
    title?: (string | null);
};
