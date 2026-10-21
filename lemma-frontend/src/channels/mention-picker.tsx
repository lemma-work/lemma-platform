"use client";

import { createContext, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { insertMention, matchMentionable, mentionAt, type Mentionable } from "./mentions";

/** Who can be named where a line is written, for the boxes that are not the
 *  composer: a line being changed. The channel's page provides it. */
export const MentionablesContext = createContext<Mentionable[]>([]);

/** The `@` menu of a box a line is written in -- the composer, and the box
 *  a line is changed in. `@` at the start or after a space opens the people
 *  and agents who can be named; arrows move, Enter or Tab picks, Escape
 *  closes it for that `@`. A pick writes "@Name " and remembers who: the
 *  words stay plain text, the people picked are kept beside them.
 *
 *  A space typed straight after a pick is the one the pick already wrote,
 *  not a second. */
export function useMentionPicker({ people, text, setText, box, onPicked }: {
    people: readonly Mentionable[];
    text: string;
    setText: (text: string) => void;
    box: RefObject<HTMLTextAreaElement | null>;
    onPicked: (who: Mentionable) => void;
}) {
    const ids = useId();
    const [caret, setCaret] = useState(0);
    const [highlight, setHighlight] = useState(0);
    const [dismissed, setDismissed] = useState<number | null>(null);
    /* Where the caret was left after a pick's own space, until anything else
       is typed. */
    const spaced = useRef<number | null>(null);

    const at = mentionAt(text, caret);
    const offered = at && dismissed !== at.start ? matchMentionable(people, at.query) : [];
    const open = offered.length > 0;
    const listId = ids + "-people";
    const chosen = Math.min(highlight, offered.length - 1);
    const list = useRef<HTMLUListElement>(null);
    const [below, setBelow] = useState(false);
    /* The menu opens above the box -- unless that would put it under
       whatever the box scrolls beneath, as on a line at the top of the
       timeline, under the channel's header. Then it opens below. */
    useLayoutEffect(() => {
        if (!open || !box.current || !list.current) return;
        const above = box.current.getBoundingClientRect().top - scrollTop(box.current);
        setBelow(above < list.current.getBoundingClientRect().height + 6);
    }, [open, offered.length, box]);

    const pick = (who: Mentionable) => {
        if (!at) return;
        const next = insertMention(text, at, caret, who);
        setText(next.text);
        onPicked(who);
        setCaret(next.caret);
        setHighlight(0);
        spaced.current = next.caret;
        requestAnimationFrame(() => {
            box.current?.focus();
            box.current?.setSelectionRange(next.caret, next.caret);
        });
    };

    /** The box's text changed. False when the change was swallowed: the
     *  space after a pick typed again. */
    const changed = (value: string, selectionStart: number | null): boolean => {
        const after = spaced.current;
        spaced.current = null;
        if (after !== null && selectionStart === after + 1 && value === text.slice(0, after) + " " + text.slice(after)) {
            requestAnimationFrame(() => box.current?.setSelectionRange(after, after));
            return false;
        }
        setText(value);
        setCaret(selectionStart ?? value.length);
        setDismissed(null);
        return true;
    };

    /** A key in the box: true when the menu took it. */
    const keyDown = (event: KeyboardEvent<HTMLTextAreaElement>): boolean => {
        if (!open || event.nativeEvent.isComposing) return false;
        if (event.key === "ArrowDown") { event.preventDefault(); setHighlight((was) => (was + 1) % offered.length); return true; }
        if (event.key === "ArrowUp") { event.preventDefault(); setHighlight((was) => (was - 1 + offered.length) % offered.length); return true; }
        if (event.key === "Enter" || event.key === "Tab") { event.preventDefault(); pick(offered[chosen]); return true; }
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); if (at) setDismissed(at.start); return true; }
        return false;
    };

    const menu = open ? (
        <ul ref={list} className={"chcompose__menu" + (below ? " chcompose__menu--below" : "")} role="listbox" id={listId} aria-label="Name someone">
            {offered.map((who, index) => (
                <li
                    key={who.kind + who.id}
                    id={listId + "-" + index}
                    role="option"
                    aria-selected={index === chosen}
                    className="chcompose__option"
                    onMouseDown={(event) => { event.preventDefault(); pick(who); }}
                >
                    <span>{who.name}</span>
                    <small>{who.kind === "AGENT" ? (who.inChannel === false ? "Agent — answers this once" : "Agent in this channel") : who.kind === "CONTACT" ? "Contact" : "Member"}</small>
                </li>
            ))}
        </ul>
    ) : null;

    /** For the textarea: it is the combobox the menu belongs to. */
    const boxProps = {
        role: "combobox",
        "aria-expanded": open,
        "aria-controls": open ? listId : undefined,
        "aria-activedescendant": open ? listId + "-" + chosen : undefined,
        "aria-autocomplete": "list" as const,
        onSelect: (event: { currentTarget: HTMLTextAreaElement }) => setCaret(event.currentTarget.selectionStart ?? 0),
    };

    return { open, menu, boxProps, changed, keyDown, setCaret };
}

/** The top of what `element` is seen through: its nearest ancestor that
 *  scrolls, else the window. */
function scrollTop(element: HTMLElement): number {
    const view = element.ownerDocument.defaultView;
    for (let at = element.parentElement; at && view; at = at.parentElement) {
        const { overflowY } = view.getComputedStyle(at);
        if (overflowY === "auto" || overflowY === "scroll") return at.getBoundingClientRect().top;
    }
    return 0;
}
