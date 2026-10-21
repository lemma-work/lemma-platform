/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentAnswers } from './AgentAnswers.js';
import type { MemberRole } from './MemberRole.js';
export type MemberUpdateRequest = {
    /**
     * An agent's.
     */
    answers?: (AgentAnswers | null);
    /**
     * A person's.
     */
    role?: (MemberRole | null);
};
