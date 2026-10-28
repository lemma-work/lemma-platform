/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { MemberAwaited } from './MemberAwaited.js';
/**
 * Who was asked to connect, and who was asked too recently to ask again.
 */
export type AskMembersResponse = {
    asked: Array<MemberAwaited>;
    /**
     * Already asked about this connector in the last day; not asked again.
     */
    asked_recently?: Array<MemberAwaited>;
    connect_url: string;
    /**
     * Not asked, because whether they were asked today could not be checked, or the line to them could not be posted, just now. Try again shortly.
     */
    not_asked?: Array<MemberAwaited>;
};
