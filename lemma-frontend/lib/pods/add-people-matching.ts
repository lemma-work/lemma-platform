/**
 * Who a typed query already names.
 *
 * Split out of the composer for the reason `vitest.config.ts` names for the
 * agent-runtime helpers and the Computers card: the decision is pure, the
 * dialog around it is not, and this is the half worth pinning down. It is also
 * the half that was wrong — an address belonging to somebody already in the pod
 * fell through every branch and landed on "Nobody by that name. Type a full
 * email address to invite them," which is the opposite of true.
 *
 * Structural types rather than the SDK's, so a test can state a case in three
 * fields instead of building a `PodMemberResponse`.
 */

/** A pod member, as the roster hands them over. */
export interface MemberLike {
    user_id: string;
    user_name?: string | null;
    /** The backend carries two addresses for a member, and either may be the
     *  one someone types. Both count. */
    user_email?: string | null;
    email?: string | null;
}

/** Somebody already sitting in the composer, not yet committed. */
export interface DraftLike {
    key: string;
    kind: 'member' | 'email';
    name?: string | null;
    email?: string | null;
}

/** A row saying there is nothing left to do about this person. */
export interface AlreadyHereRow {
    key: string;
    name: string;
    note: string;
}

/** How many such rows are worth showing before the list stops being scannable. */
const ALREADY_HERE_LIMIT = 5;

function lower(value: string | null | undefined): string {
    return (value || '').toLowerCase();
}

/** Every address the pod already knows a member by. */
export function podMemberEmails(members: MemberLike[]): Set<string> {
    const emails = new Set<string>();
    for (const member of members) {
        for (const address of [member.user_email, member.email]) {
            const normalized = lower(address);
            if (normalized) emails.add(normalized);
        }
    }
    return emails;
}

/**
 * Whether an address is one we could still invite.
 *
 * False for anyone the pod already holds, anyone the organization already holds
 * (they get added, not mailed), and anyone already queued in the composer.
 */
export function offersInviteFor({
    query,
    members,
    candidateEmails,
    draftedEmails,
}: {
    query: string;
    members: MemberLike[];
    candidateEmails: Iterable<string | null | undefined>;
    draftedEmails: Iterable<string | null | undefined>;
}): boolean {
    const needle = lower(query.trim());
    if (!needle) return false;

    const drafted = new Set([...draftedEmails].map(lower));
    if (drafted.has(needle)) return false;

    const candidates = new Set([...candidateEmails].map(lower));
    if (candidates.has(needle)) return false;

    return !podMemberEmails(members).has(needle);
}

/**
 * The people this query names who cannot be added again.
 *
 * Matches on every name and address, not just the one the field happens to be
 * showing — someone typing a colleague's address should not be told that
 * colleague does not exist because the roster lists them by name.
 */
export function resolveAlreadyHere({
    query,
    members,
    drafts,
}: {
    query: string;
    members: MemberLike[];
    drafts: DraftLike[];
}): AlreadyHereRow[] {
    const needle = lower(query.trim());
    if (!needle) return [];

    const names = (...values: Array<string | null | undefined>) =>
        values.some((value) => lower(value).includes(needle));

    const inPod: AlreadyHereRow[] = members
        .filter((member) => names(member.user_name, member.user_email, member.email))
        .map((member) => ({
            key: `in-pod:${member.user_id}`,
            name: member.user_name || member.user_email || member.email || member.user_id,
            note: 'Already a member',
        }));

    const queued: AlreadyHereRow[] = drafts
        .filter((draft) => names(draft.kind === 'member' ? draft.name : null, draft.email))
        .map((draft) => ({
            key: `queued:${draft.key}`,
            name: (draft.kind === 'member' ? draft.name : draft.email) || draft.key,
            note: 'Already added',
        }));

    return [...inPod, ...queued].slice(0, ALREADY_HERE_LIMIT);
}
