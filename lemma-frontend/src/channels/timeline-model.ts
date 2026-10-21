import type { Line } from "./model";

/** What the timeline works out about where the reader is: where the unread
 *  lines start, and what came in while they were reading back. Pure, so the
 *  test runner holds it without a page. */

const someoneElse = (line: Pick<Line, "sender">, me: string | null) => !(line.sender.kind === "MEMBER" && line.sender.id === me);

/** The first line the reader had not read when they opened the channel --
 *  the newest line past `readSeq` said by somebody else -- or null. The
 *  reader's own lines are never news to them. */
export function firstUnread(lines: readonly Pick<Line, "id" | "seq" | "sender">[], readSeq: number, me: string | null): string | null {
    for (const line of lines) if (line.seq > readSeq && someoneElse(line, me)) return line.id;
    return null;
}

/** Whether unread lines start before the oldest line held: reading back
 *  brings them. */
export function unreadBeforeHeld(lines: readonly Pick<Line, "seq">[], readSeq: number, hasOlder: boolean): boolean {
    const oldest = lines.find((line) => line.seq > 0)?.seq ?? null;
    return hasOlder && oldest !== null && readSeq + 1 < oldest;
}

/** How many lines from others came in after `sinceSeq`: the "new lines"
 *  pill while the reader is up the timeline. */
export function newSince(lines: readonly Pick<Line, "seq" | "sender">[], sinceSeq: number, me: string | null): number {
    let count = 0;
    for (const line of lines) if (line.seq > sinceSeq && someoneElse(line, me)) count += 1;
    return count;
}

/** The pill's words. */
export function sayNewLines(count: number): string {
    return count === 1 ? "1 new line" : count + " new lines";
}

/** How long after one message the same person's next is drawn as part of
 *  it -- no face, no name -- the way a chat groups a run of messages. */
export const GROUP_WITHIN_MS = 5 * 60_000;

/** Whether `line` carries on from `before`: the same sender, for the same
 *  person, within a few minutes, and nothing between them worth a new head --
 *  a deletion, a note, a question, a thread. */
export function continues(
    before: Pick<Line, "sender" | "onBehalfOf" | "createdAt" | "deleted" | "membersOnly" | "interaction" | "replyCount"> | undefined,
    line: Pick<Line, "sender" | "onBehalfOf" | "createdAt" | "deleted" | "membersOnly">,
): boolean {
    if (!before || before.deleted || line.deleted) return false;
    if (before.interaction || before.replyCount > 0) return false;
    if (before.sender.kind !== line.sender.kind || before.sender.id !== line.sender.id || before.sender.name !== line.sender.name) return false;
    if ((before.onBehalfOf?.id ?? null) !== (line.onBehalfOf?.id ?? null)) return false;
    if (before.membersOnly !== line.membersOnly) return false;
    const gap = Date.parse(line.createdAt) - Date.parse(before.createdAt);
    return Number.isFinite(gap) && gap >= 0 && gap < GROUP_WITHIN_MS;
}

/** The line an answer answers, when it is not the line just above it --
 *  somebody wrote in between -- so the answer can say what it is answering.
 *  Nothing for an answer right under its question, or one in a thread. */
export function answeredOutOfPlace<T extends Pick<Line, "id" | "replyToId" | "threadRootId">>(
    line: T,
    above: Pick<Line, "id"> | undefined,
    byId: ReadonlyMap<string, T>,
): T | null {
    if (!line.replyToId || line.threadRootId || above?.id === line.replyToId) return null;
    return byId.get(line.replyToId) ?? null;
}
