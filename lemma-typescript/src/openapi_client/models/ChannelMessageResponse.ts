/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SenderKind } from './SenderKind.js';
export type ChannelMessageResponse = {
    created_at: string;
    id: string;
    members_only?: boolean;
    /**
     * For an agent's line: the contact it answered.
     */
    on_behalf_contact_id?: (string | null);
    /**
     * For an agent's line: the member it acted for.
     */
    on_behalf_user_id?: (string | null);
    sender_agent_id?: (string | null);
    sender_contact_id?: (string | null);
    sender_kind: SenderKind;
    sender_name?: (string | null);
    sender_user_id?: (string | null);
    /**
     * Where it was said; absent for a line written in Lemma.
     */
    source_id?: (string | null);
    text: string;
};
