"use client";

import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { LineView } from "./line";
import type { Answering } from "./live";
import { lineDays, type ChannelDetail, type Line } from "./model";
import { useSeenSeq } from "./read-state";
import { answeredOutOfPlace, continues, firstUnread, newSince, sayNewLines, unreadBeforeHeld } from "./timeline-model";

/** How many older pages "Jump to unread" reads back, at most, to reach the
 *  first unread line. */
const JUMP_PAGES = 8;

/** A channel's lines, a day at a time, oldest at the top. It stays at the
 *  newest line while you are there, keeps your place while you read back,
 *  and keeps it again when an older page is put above.
 *
 *  Where the unread lines started when the channel was opened is marked
 *  ("New"), with a way to jump there when it is out of sight above; lines
 *  that come in while you read back are counted on a pill that takes you
 *  down to them. Opened at one line -- from search or the bell -- it goes
 *  there once, and lets go as soon as the reader scrolls: it never pulls
 *  them back to it.
 *
 *  It tells its page how far the reader has seen (`onProgress`) -- the
 *  newest line on screen, and whether they are at the bottom -- which is what
 *  marks the channel read. Only new lines are said to a screen reader
 *  (`announced`), not the history as it loads. */
export function Timeline({ podId, channel, lines, loading, failed, hasOlder, loadingOlder, onOlder, onRetry, onThread, pin, visible,
    mayWrite, answering, agentName, highlight, announced, onProgress, unreadFrom = null, me = null, intro = null }: {
    podId: string;
    channel: ChannelDetail;
    lines: Line[];
    loading: boolean;
    failed: boolean;
    hasOlder: boolean;
    loadingOlder: boolean;
    onOlder: () => void;
    onRetry: () => void;
    onThread?: (line: Line) => void;
    /** Bumped when the reader sends something: back to the newest line. */
    pin: number;
    visible: boolean;
    mayWrite: boolean;
    answering: Answering[];
    agentName: (agentId: string | null) => string;
    /** A line to draw out and scroll to: opened from search or the bell. */
    highlight: string | null;
    announced: string;
    onProgress: (progress: { seenMax: number; atEnd: boolean }) => void;
    /** The reader's read position when they opened the channel: the "New"
     *  mark goes after it, and stays put while they read. */
    unreadFrom?: number | null;
    me?: string | null;
    /** What the chat is, at the very top once there is nothing older. */
    intro?: ReactNode;
}) {
    const box = useRef<HTMLDivElement>(null);
    const atEnd = useRef(true);
    const [endState, setEndState] = useState(true);
    const fromEnd = useRef<number | null>(null);
    const days = useMemo(() => lineDays(lines), [lines]);
    const byId = useMemo(() => new Map(lines.map((line) => [line.id, line])), [lines]);
    const newest = lines.at(-1)?.id ?? null;
    const newestSeq = lines.at(-1)?.seq ?? 0;
    const seenMax = useSeenSeq(box, lines.length + ":" + newest, visible);
    /* The highlight already gone to, or given up on because the reader
       moved first: either way it is never scrolled to again. */
    const placed = useRef<string | null>(null);
    /* Where the reader left the bottom: lines after it are the pill's. */
    const [leftAt, setLeftAt] = useState<number | null>(null);
    const unread = unreadFrom === null ? null : firstUnread(lines, unreadFrom, me);
    /* Unread lines older than every page held: jumping reads back to them. */
    const unreadEarlier = unreadFrom !== null && unreadBeforeHeld(lines, unreadFrom, hasOlder);
    const [unreadAbove, setUnreadAbove] = useState(false);
    const [jumping, setJumping] = useState(0);
    const fresh = leftAt === null ? 0 : newSince(lines, leftAt, me);

    useEffect(() => { onProgress({ seenMax, atEnd: endState }); }, [seenMax, endState, onProgress]);

    const settled = () => !highlight || placed.current === highlight;

    useLayoutEffect(() => {
        const element = box.current;
        if (!element) return;
        if (fromEnd.current !== null) {
            element.scrollTop = element.scrollHeight - fromEnd.current;
            fromEnd.current = null;
            return;
        }
        if (atEnd.current && settled()) element.scrollTop = element.scrollHeight;
    }, [lines.length, newest, answering.length, highlight]);

    const toEnd = () => {
        const element = box.current;
        atEnd.current = true;
        setEndState(true);
        setLeftAt(null);
        if (element) element.scrollTop = element.scrollHeight;
    };
    useLayoutEffect(toEnd, [pin]);

    /* Opened at one line: there, once it has loaded -- and only once. */
    useEffect(() => {
        if (!highlight || placed.current === highlight) return;
        const element = box.current?.querySelector<HTMLElement>("#line-" + CSS.escape(highlight));
        if (!element) return;
        element.scrollIntoView({ block: "center", inline: "nearest" });
        placed.current = highlight;
        atEnd.current = false;
        setEndState(false);
    }, [highlight, lines.length]);

    /* "Jump to unread": read back until the first unread line is held. */
    useEffect(() => {
        if (!jumping) return;
        const element = unread ? box.current?.querySelector<HTMLElement>("#line-" + CSS.escape(unread)) : null;
        if (element && !unreadEarlier) {
            element.scrollIntoView({ block: "start", inline: "nearest" });
            setJumping(0);
            return;
        }
        if (hasOlder && !loadingOlder && jumping <= JUMP_PAGES) {
            setJumping((count) => count + 1);
            older();
        } else if (!hasOlder || jumping > JUMP_PAGES) {
            setJumping(0);
        }
    }, [jumping, unread, unreadEarlier, hasOlder, loadingOlder, lines.length]);

    const older = () => {
        const element = box.current;
        if (element) fromEnd.current = element.scrollHeight - element.scrollTop;
        onOlder();
    };

    /* The reader moved: a highlight not reached yet is let go. */
    const moved = () => { if (highlight) placed.current = highlight; };

    const measure = (element: HTMLDivElement) => {
        const mark = unread ? element.querySelector<HTMLElement>("#line-" + CSS.escape(unread)) : null;
        setUnreadAbove(unreadEarlier || Boolean(mark && mark.offsetTop < element.scrollTop));
    };
    useEffect(() => { if (box.current) measure(box.current); }, [unread, unreadEarlier, lines.length]);

    return (
        <div className="ctimeline__wrap">
            {unreadAbove && (
                <button type="button" className="ghost-pill ctimeline__jump" onClick={() => setJumping(1)} disabled={jumping > 0}>
                    {jumping > 0 ? "Reading back…" : "Jump to unread"}
                </button>
            )}
            <div
                className="ctimeline"
                ref={box}
                tabIndex={0}
                aria-label="Messages"
                onWheel={moved}
                onTouchMove={moved}
                onKeyDown={(event) => { if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) moved(); }}
                onScroll={(event) => {
                    const element = event.currentTarget;
                    const end = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
                    atEnd.current = end;
                    if (end !== endState) setEndState(end);
                    if (end) setLeftAt(null);
                    else if (leftAt === null) setLeftAt(newestSeq);
                    measure(element);
                }}
            >
                {hasOlder && (
                    <button type="button" className="ghost-pill ctimeline__older" disabled={loadingOlder} onClick={older}>
                        {loadingOlder ? "Loading…" : "Show earlier messages"}
                    </button>
                )}
                {loading && <p className="ctimeline__quiet" role="status">Loading…</p>}
                {failed && (
                    <p className="ctimeline__quiet" role="alert">
                        Couldn’t load the messages. <button type="button" className="gsheet__inline" onClick={onRetry}>Try again</button>
                    </p>
                )}
                {!loading && !failed && !hasOlder && intro}
                {!loading && !failed && lines.length === 0 && !intro && <p className="ctimeline__quiet">No messages yet.</p>}
                <div>
                    {days.map((day) => (
                        <section key={day.key} className="cday" aria-label={day.label}>
                            <h2 className="cday__label"><span>{day.label}</span></h2>
                            <ol className="cday__lines">
                                {day.lines.map((line, index) => (
                                    <Fragment key={line.id}>
                                        {line.id === unread && (
                                            <li className="ctimeline__unread" aria-label="New since you last read">
                                                <span>New</span>
                                            </li>
                                        )}
                                        <LineView
                                            podId={podId}
                                            channel={channel}
                                            line={line}
                                            onThread={onThread}
                                            mayWrite={mayWrite}
                                            answering={answering.filter((one) => one.messageId === line.id)}
                                            agentName={agentName}
                                            highlighted={highlight === line.id}
                                            visible={visible}
                                            continued={line.id !== unread && continues(day.lines[index - 1], line)}
                                            quote={answeredOutOfPlace(line, day.lines[index - 1], byId)}
                                        />
                                    </Fragment>
                                ))}
                            </ol>
                        </section>
                    ))}
                </div>
                {/* New lines only: the history loading is not news. */}
                <p className="sr-only" aria-live="polite" aria-atomic="true">{announced}</p>
            </div>
            {!endState && fresh > 0 && (
                <button type="button" className="pill-button ctimeline__fresh" onClick={toEnd}>
                    {sayNewLines(fresh)} ↓
                </button>
            )}
        </div>
    );
}
