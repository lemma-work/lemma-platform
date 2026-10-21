/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentAnswers } from './AgentAnswers.js';
import type { MemberRole } from './MemberRole.js';
import type { PersonRef } from './PersonRef.js';
export type ChannelMemberResponse = {
    /**
     * An agent's: when it answers here.
     */
    answers?: (AgentAnswers | null);
    joined_at?: (string | null);
    /**
     * A member, a contact, or an agent.
     */
    person: PersonRef;
    role: MemberRole;
};
