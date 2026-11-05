/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { FilePart } from './FilePart.js';
import type { LineDelivery } from './LineDelivery.js';
import type { LinkPart } from './LinkPart.js';
import type { MentionPart } from './MentionPart.js';
import type { PersonRef } from './PersonRef.js';
import type { QuestionResponse } from './QuestionResponse.js';
export type ChannelMessageResponse = {
    /**
     * The reader is the member an `asker_only` answer was made for, and may share it with the channel (`…/messages/{id}/share`).
     */
    can_share?: boolean;
    channel_id: string;
    /**
     * On the reader's own line: the name their client gave it, as sent (`client_message_id`, or `Idempotency-Key`).
     */
    client_message_id?: (string | null);
    conversation_id?: (string | null);
    created_at: string;
    deleted?: boolean;
    /**
     * Each place outside Lemma the line went to, and what became of it -- on every line the reader may read. Pushes to members are not listed here (`…/deliveries` has the writer's every send).
     */
    delivered?: (Array<LineDelivery> | null);
    edited_at?: (string | null);
    id: string;
    last_reply_at?: (string | null);
    members_only?: boolean;
    /**
     * For an agent's line: the member or contact it acted for, whose access the answer was made with.
     */
    on_behalf_of?: (PersonRef | null);
    parts?: Array<(MentionPart | FilePart | LinkPart)>;
    question?: (QuestionResponse | null);
    /**
     * On a deleted line: the member who removed it, when that was not who wrote it -- somebody who may change the channel.
     */
    removed_by?: (PersonRef | null);
    /**
     * On a thread's first line.
     */
    reply_count?: number;
    reply_to_id?: (string | null);
    sender: PersonRef;
    /**
     * Its place in the channel's timeline.
     */
    seq: number;
    /**
     * When it was shared with the channel: by the member it was made for, or as it was posted, in a group that shows agents' answers to everybody (`answers_shared`).
     */
    shared_at?: (string | null);
    /**
     * Where it was said or sent; absent for a line only in Lemma.
     */
    source_id?: (string | null);
    /**
     * Absent where the line is withheld from the reader, or deleted.
     */
    text?: (string | null);
    /**
     * The line this one is a reply in the thread of.
     */
    thread_root_id?: (string | null);
    /**
     * `asker_only`: an agent's answer made with one member's own access, read in full by that member alone; everybody else here is shown it `withheld`. `channel`: everybody here reads it.
     */
    visibility?: ChannelMessageResponse.visibility;
    /**
     * An answer an agent made with another member's own access: the reader sees that it was given, and for whom, not what it said.
     */
    withheld?: boolean;
};
export namespace ChannelMessageResponse {
    /**
     * `asker_only`: an agent's answer made with one member's own access, read in full by that member alone; everybody else here is shown it `withheld`. `channel`: everybody here reads it.
     */
    export enum visibility {
        ASKER_ONLY = 'asker_only',
        CHANNEL = 'channel',
    }
}
