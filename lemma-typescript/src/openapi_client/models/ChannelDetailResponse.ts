/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AttentionResponse } from './AttentionResponse.js';
import type { ChannelKind } from './ChannelKind.js';
import type { ChannelMemberResponse } from './ChannelMemberResponse.js';
import type { ChannelSourceResponse } from './ChannelSourceResponse.js';
import type { ContactStatus } from './ContactStatus.js';
import type { LinePreview } from './LinePreview.js';
import type { MyMembershipResponse } from './MyMembershipResponse.js';
import type { PersonRef } from './PersonRef.js';
import type { SourceSettingsResponse } from './SourceSettingsResponse.js';
import type { SourceSummary } from './SourceSummary.js';
import type { UnreadResponse } from './UnreadResponse.js';
import type { Visibility } from './Visibility.js';
export type ChannelDetailResponse = {
    /**
     * A contact's channel: whether its agents answer the contact.
     */
    agents_answer?: (boolean | null);
    /**
     * A group's: whether an answer an agent gives a member here with their own access is read by everybody in it (the default), or by that member alone until they share it. Absent for another kind.
     */
    answers_shared?: (boolean | null);
    archived?: boolean;
    /**
     * A contact's channel: who answers them by hand.
     */
    assignee?: (PersonRef | null);
    /**
     * A contact's channel: its current case asks for a person, and why. Never set on another kind of channel.
     */
    attention?: (AttentionResponse | null);
    /**
     * A contact's channel: its current case, the one `status`, `assignee` and `attention` are read from (`/cases` lists them all).
     */
    case_id?: (string | null);
    created_at: string;
    /**
     * A contact's channel: how long the assignee holds the contact after their last line before the agent answers again.
     */
    hand_back_after_hours?: (number | null);
    id: string;
    /**
     * Whether the reader is in it. A pod admin listing a bot's groups (`source_surface`) is shown its private ones too, not joined and without their last line, until they join.
     */
    joined?: boolean;
    kind: ChannelKind;
    last_line?: (LinePreview | null);
    last_line_at: string;
    last_seq?: number;
    me?: MyMembershipResponse;
    members?: Array<ChannelMemberResponse>;
    /**
     * A contact's channel: whether they asked the pod to stop writing to a handle of theirs -- lines to it are not sent until they write from it again (the contact's `identities` say which).
     */
    opted_out?: (boolean | null);
    /**
     * Whose channel it is: the member's for a DIRECT one, the contact's for a CONTACT one; absent for a GROUP.
     */
    person?: (PersonRef | null);
    source_places?: Array<ChannelSourceResponse>;
    /**
     * Listed by `source_surface`: that bot's settings for its place here -- who answers its people outside the pod, and whether they are.
     */
    source_settings?: (SourceSettingsResponse | null);
    /**
     * The outside places feeding it.
     */
    sources?: Array<SourceSummary>;
    /**
     * A contact's channel: where it stands.
     */
    status?: (ContactStatus | null);
    title?: (string | null);
    topic?: (string | null);
    unread?: UnreadResponse;
    visibility: Visibility;
};
