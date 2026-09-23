"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { isForbidden } from "@/session/auth-state";
import { BackIcon, ChevronLeftIcon, ChevronRightIcon, RefreshIcon, WarningIcon } from "@/ui/icons";
import { FileView } from "@/thread/file-view";
import { markTint } from "@/shell/mark";
import { hashSeed } from "@/identity/seeded-identity";
import { splitDescription } from "./skill-frontmatter";
import { readSkills, skillsKey, SKILLS_FRESH } from "./read-skills";
import { sayUnloadable, type SkillCard } from "./skills";


/** A teammate's skills, as a hand of cards.
 *
 *  Cards rather than rows, and this is the one section on this page where that
 *  is the right answer. The lists around it — agents, workflows, schedules —
 *  are flat rows on a divider because each is a name you scan past on the way
 *  to the one you wanted. A skill is not that. A pod has a handful, each is a
 *  discrete named capability with a sentence explaining itself, and together
 *  they are the most concrete answer there is to "what can this teammate
 *  actually do". That is a hand you deal through, not a list you scan.
 *
 *  So they behave like cards. They sit in a track you move along, they have a
 *  rank in the corner, and they turn over: the front is what the skill is, the
 *  back is where it lives. One gesture, done properly, rather than three
 *  half-done — the flip is the whole conceit and the hover lift is just enough
 *  to say the thing is liftable.
 *
 *  It shares the badge's idea without borrowing its clothes: the badge is
 *  identity *issued* and prints at full strength in the teammate's own field
 *  colour, one figure on the page. A hand of five in that colour would be five
 *  figures shouting, so a skill gets a band instead of a field — the same
 *  seeded hue the app tints every other mark with, four pixels of it.
 *
 *  Three things this must not stop doing, whatever else changes:
 *  every card is reachable with a keyboard, the track never pushes the page
 *  sideways at 375px, and a skill whose SKILL.md could not be read still gets
 *  a card saying so instead of being quietly left out of the deck.
 */
export function SkillsView({ podId, teammate, onFile, onCreate }: {
    podId: string;
    /** Whose skills these are, so an empty deck can say whose. */
    teammate: string;
    /** Open a SKILL.md as a file tab, the way the Library hands a path up.
     *
     *  Optional, and where it is absent the card opens the file here instead.
     *  The profile is reached from the shell, which owns the tab strip; until
     *  it passes one down, a card that did nothing when turned over would be
     *  worse than a card that opens in place. */
    onFile?: (path: string) => void;
    /** Ask the teammate for a new one. Not a form: a skill is a folder with a
     *  `SKILL.md` in it, and the platform ships `lemma-skill-creator` whose
     *  whole job is writing one properly — triggers, instructions, resources.
     *  A dialog here would be this app inventing a worse version of a skill
     *  the teammate already has. */
    onCreate?: () => void;
}) {
    const cache = useQueryClient();
    const [open, setOpen] = useState<string | null>(null);

    const skills = useQuery({
        queryKey: skillsKey(podId),
        queryFn: () => readSkills(podId, cache),
        staleTime: SKILLS_FRESH,
    });

    const cards = skills.data ?? [];
    const showing = open ? cards.find((card) => card.folder === open) : undefined;
    const unloadable = sayUnloadable(cards);

    if (showing) {
        return (
            <div className="skill-read">
                <div className="skill-read__head">
                    <button className="skill-read__back" onClick={() => setOpen(null)}>
                        <BackIcon size={15} aria-hidden="true" />All skills
                    </button>
                    <code className="skill-read__path">{showing.file}</code>
                </div>
                {showing.problem && (
                    <p className="skill-flag skill-flag--wide" role="note">
                        <WarningIcon size={15} aria-hidden="true" />
                        {showing.problem}
                    </p>
                )}
                {/* The same reader the file tab uses, on the same cache key the
                    deck already filled — so opening a skill costs no request
                    and looks the same here as it does anywhere else. */}
                <FileView podId={podId} path={showing.file} full />
            </div>
        );
    }

    return (
        <>
            {skills.isPending && <p className="empty-row" role="status">Reading the skills…</p>}
            {skills.isError && (
                <p className="empty-row" role="alert">
                    {isForbidden(skills.error)
                        ? "You may not read this teammate’s skills."
                        : "Couldn’t load skills."}{" "}
                    <button className="linkish" onClick={() => void skills.refetch()}>Try again</button>
                </p>
            )}
            {skills.isSuccess && cards.length === 0 && (
                <p className="empty-row">
                    {teammate} has no saved skills yet. Ask it to save what it has learned about a task you repeat.
                </p>
            )}

            {/* The deck draws whenever there is something in it *or* somewhere
                to go: a pod with no skills yet is the case that most needs the
                offer, and it was the one case that rendered nothing at all. */}
            {(cards.length > 0 || onCreate) && (
                <Deck
                    cards={cards}
                    teammate={teammate}
                    onRead={(card) => (onFile ? onFile(card.file) : setOpen(card.folder))}
                    onCreate={onCreate}
                />
            )}
            {unloadable && <p className="skill-tally">{unloadable}</p>}
        </>
    );
}

/** Whether this browser has been asked for less movement.
 *
 *  Read rather than assumed, because the three moving parts here are in three
 *  different places: the flip and the lift are CSS and answer the media query
 *  themselves, but the track's scrolling is a script call that has to be told.
 *  Somebody who turned motion down and still got a smoothly gliding carousel
 *  would have been ignored by the one part that could not read the stylesheet. */
function stillnessWanted(): boolean {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** The hand, and the ways through it.
 *
 *  A scroll container rather than a transformed strip, which decides most of
 *  the accessibility for free: a focused card is scrolled into view by the
 *  browser itself, a trackpad swipe and a touch drag already work, and the
 *  track cannot push the page sideways because its own width is whatever the
 *  column gives it. What is left to write is the parts a scroll container does
 *  not do — arrows, a mouse drag, and the arrow keys.
 */
function Deck({ cards, teammate, onRead, onCreate }: { cards: SkillCard[]; teammate: string; onRead: (card: SkillCard) => void; onCreate?: () => void }) {
    const track = useRef<HTMLUListElement>(null);
    const [edges, setEdges] = useState({ start: true, end: false });

    const measure = useCallback(() => {
        const rail = track.current;
        if (!rail) return;
        /* A pixel of slack at each end. Scroll positions are fractional on a
           zoomed page and on a trackpad, so `scrollLeft === 0` is a test that
           is false while the track is visibly at its start — which left the
           left arrow live with nowhere to go. */
        setEdges({
            start: rail.scrollLeft <= 1,
            end: rail.scrollLeft + rail.clientWidth >= rail.scrollWidth - 1,
        });
    }, []);

    useEffect(() => {
        measure();
        const rail = track.current;
        if (!rail || typeof ResizeObserver === "undefined") return;
        const watcher = new ResizeObserver(measure);
        watcher.observe(rail);
        return () => watcher.disconnect();
    }, [measure, cards.length]);

    /** One card plus the gap, measured rather than hard-coded: the card width
     *  is a CSS decision and a number copied into here is a number that goes
     *  stale the first time the stylesheet changes. */
    function step(rail: HTMLUListElement): number {
        const first = rail.firstElementChild as HTMLElement | null;
        if (!first) return rail.clientWidth;
        const gap = Number.parseFloat(getComputedStyle(rail).columnGap) || 0;
        return first.getBoundingClientRect().width + gap;
    }

    function nudge(direction: -1 | 1) {
        const rail = track.current;
        if (!rail) return;
        rail.scrollBy({ left: direction * step(rail), behavior: stillnessWanted() ? "auto" : "smooth" });
    }

    /* Dragging. `dragged` is not a nicety: without it the pointer-up at the
       end of a drag lands as a click on whichever card is under the cursor,
       so throwing the hand across turns a card over. */
    const drag = useRef<{ from: number; at: number } | null>(null);
    const dragged = useRef(false);

    function onPointerDown(event: React.PointerEvent<HTMLUListElement>) {
        /* Only a mouse. A touch or a pen already scrolls this natively, and
           taking the pointer away from the browser means reimplementing
           momentum badly. */
        if (event.pointerType !== "mouse" || event.button !== 0) return;
        const rail = track.current;
        if (!rail) return;
        drag.current = { from: event.clientX, at: rail.scrollLeft };
        dragged.current = false;
    }

    function onPointerMove(event: React.PointerEvent<HTMLUListElement>) {
        const rail = track.current;
        const held = drag.current;
        if (!rail || !held) return;
        const moved = event.clientX - held.from;
        if (Math.abs(moved) > 4) dragged.current = true;
        if (dragged.current) {
            rail.scrollLeft = held.at - moved;
            /* Only once it is a drag rather than a jittery click, or a plain
               click on a card would have its selection cancelled. */
            event.preventDefault();
        }
    }

    function endDrag() {
        drag.current = null;
        /* Cleared after the click that follows this pointer-up, not during
           it. */
        setTimeout(() => { dragged.current = false; }, 0);
    }

    /** Left and right along the hand, because a card strip that answers only a
     *  trackpad does not exist for a lot of people. Focus moves and the
     *  browser brings the card into view; Home and End go to the ends. */
    function onKeyDown(event: React.KeyboardEvent<HTMLUListElement>) {
        const keys = ["ArrowRight", "ArrowLeft", "Home", "End"];
        if (!keys.includes(event.key)) return;
        const rail = track.current;
        if (!rail) return;
        const stops = Array.from(rail.querySelectorAll<HTMLElement>("[data-stop]"));
        if (stops.length === 0) return;
        const here = stops.findIndex((stop) => stop.contains(document.activeElement));
        const next =
            event.key === "Home" ? 0
            : event.key === "End" ? stops.length - 1
            : event.key === "ArrowRight" ? Math.min(stops.length - 1, here + 1)
            : Math.max(0, here - 1);
        if (next === here && event.key.startsWith("Arrow")) return;
        event.preventDefault();
        stops[next]?.focus();
    }

    return (
        <div className="skill-hand">
            <ul
                className="skill-track"
                ref={track}
                onScroll={measure}
                onKeyDown={onKeyDown}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={endDrag}
                onPointerCancel={endDrag}
                onPointerLeave={endDrag}
            >
                {/* First in the track, because the empty deck has to offer
                    something and because "make another" is the one action this
                    section has. A card rather than a button beside the
                    heading: it is the same object as the things beside it and
                    it scrolls with them, so it cannot end up stranded off the
                    side on a narrow pane. */}
                {onCreate && (
                    <li className="skill-card skill-card--new">
                        <button className="skill-new" onClick={onCreate}>
                            <span className="skill-new__plus" aria-hidden="true">+</span>
                            <span className="skill-new__name">New skill</span>
                            <span className="skill-new__note">Ask {teammate} to write one</span>
                        </button>
                    </li>
                )}
                {cards.map((card) => (
                    <Card
                        key={card.folder}
                        card={card}
                        onRead={() => onRead(card)}
                        blocked={() => dragged.current}
                    />
                ))}
            </ul>
            {/* Below the hand rather than over it: an arrow floating on the
                first and last card covers the thing it is there to help you
                reach, and at 375px there is no margin to put it in. */}
            <div className="skill-oars">
                <button
                    className="skill-oar"
                    onClick={() => nudge(-1)}
                    disabled={edges.start}
                    aria-label="Earlier skills"
                ><ChevronLeftIcon size={16} /></button>
                <span className="skill-oars__count" aria-hidden="true">
                    {cards.length} {cards.length === 1 ? "skill" : "skills"}
                </span>
                <button
                    className="skill-oar"
                    onClick={() => nudge(1)}
                    disabled={edges.end}
                    aria-label="Later skills"
                ><ChevronRightIcon size={16} /></button>
            </div>
        </div>
    );
}

/** The marks a card can wear.
 *
 *  Line drawings rather than filled ones, and abstract rather than literal: a
 *  magnifying glass on a research skill would be a guess at what the skill is
 *  for, and the next skill along would want a glass too. These say *which*
 *  card, not what it does — the job the suit and rank do on a playing card,
 *  which is why there are a handful rather than one per skill.
 *
 *  `currentColor`, so each takes the card's own seeded colour without a second
 *  place deciding it.
 */
const SKILL_GLYPHS = [
    <svg key="circle" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.4">
        <circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3" />
    </svg>,
    <svg key="cube" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M12 3 20 7.5v9L12 21 4 16.5v-9Z" /><path d="M4 7.5 12 12l8-4.5M12 12v9" />
    </svg>,
    <svg key="rings" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.4">
        <circle cx="9" cy="12" r="6" /><circle cx="15" cy="12" r="6" />
    </svg>,
    <svg key="stack" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M12 3 21 8l-9 5-9-5Z" /><path d="M3 12.5 12 17.5 21 12.5" />
    </svg>,
    <svg key="arrow" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M4 20 20 4M20 4h-7M20 4v7" /><circle cx="6.5" cy="17.5" r="2.5" />
    </svg>,
    <svg key="grid" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.4">
        <rect x="4" y="4" width="7" height="7" rx="1" /><rect x="13" y="4" width="7" height="7" rx="1" />
        <rect x="4" y="13" width="7" height="7" rx="1" /><circle cx="16.5" cy="16.5" r="3.5" />
    </svg>,
];

/** 820, then 1.2k. A four-figure number in a corner pip stops being a pip. */
function shortCount(words: number): string {
    if (words < 1000) return String(words);
    return (words / 1000).toFixed(1).replace(/\.0$/, "") + "k";
}


/** One card, with a front and a back.
 *
 *  Two faces in the markup rather than one face whose contents swap, because
 *  the back carries buttons and the front carries a button, and a button
 *  inside a button is invalid markup the browser silently unnests — which is
 *  how "rename" would once have opened a file in the Library. `inert` on the
 *  face that is turned away is what keeps the other one's controls out of the
 *  tab order and away from a screen reader while it is edge-on.
 */
function Card({ card, onRead, blocked }: {
    card: SkillCard;
    onRead: () => void;
    /** Whether the hand is mid-throw. A pointer-up at the end of a drag
     *  arrives as a click, and a card that turns over because somebody scrolled
     *  past it is a card fighting its own container. */
    blocked: () => boolean;
}) {
    const [back, setBack] = useState(false);
    const tint = markTint(card.folder);
    const split = splitDescription(card.description);
    /* One of a handful of line marks, chosen by the same hash that picks the
       colour. Not meaning — no glyph can say what a skill does — but identity:
       the pairing is stable, so the card is recognisable before it is read. */
    const glyph = SKILL_GLYPHS[hashSeed(card.folder + ":glyph") % SKILL_GLYPHS.length];

    return (
        <li
            className="skill-card"
            data-broken={card.problem ? "" : undefined}
            data-back={back ? "" : undefined}
            style={{ ["--band" as string]: tint.color, ["--face" as string]: tint.background }}
        >
            <div className="skill-card__inner">
                <div className="skill-card__face" inert={back || undefined}>
                    {/* No `aria-label`: it would replace everything below it,
                        and what is below it is the skill. The name comes from
                        the content, in the order the card reads. */}
                    <button
                        className="skill-card__front"
                        data-stop=""
                        onClick={() => { if (!blocked()) setBack(true); }}
                        aria-expanded={back}
                    >
                        <span className="skill-card__band" aria-hidden="true" />
                        {/* The eyebrow and the mark: what kind of object this
                            is, and which one. The mark's glyph and colour are
                            both seeded on the folder name, so a skill looks
                            the same every time anybody opens this — which is
                            most of what makes a set of cards read as a set
                            rather than a list with rounded corners. */}
                        <span className="skill-card__eyebrow">
                            <i>Skill</i>
                            <span className="skill-card__mark" aria-hidden="true">{glyph}</span>
                        </span>
                        <span className="skill-card__name">{card.title}</span>
                        {/* The front is identity and nothing else: what kind
                            of object, which one, and how much of it there is.
                            What it does and when to reach for it moved to the
                            back — they are what you turn a card over for, and
                            keeping them on the face made every card tall
                            enough to need three of them to fill a row. */}
                        {card.problem ? (
                            <span className="skill-flag">
                                <WarningIcon size={14} aria-hidden="true" />
                                {card.problem}
                            </span>
                        ) : split.does ? (
                            <span className="skill-card__line">{split.does}</span>
                        ) : card.read ? (
                            <span className="skill-card__line skill-card__desc--none">
                                No description yet.
                            </span>
                        ) : null}
                        <span className="skill-card__foot">
                            {card.words > 0 && <em>{shortCount(card.words)} words</em>}
                            <i className="skill-card__turnhint" aria-hidden="true">
                                <RefreshIcon size={12} />turn
                            </i>
                        </span>
                        <span className="sr-only">
                            {card.words > 0 && card.words + " words of instruction. "}
                            Turn over for where it lives.
                        </span>
                    </button>
                </div>

                <div className="skill-card__face skill-card__face--rear" inert={!back || undefined}>
                    {/* Solid, in the card's own seeded colour, and almost
                        empty on purpose. It held the name, two labelled rows
                        and the path — a second front, which is not what
                        turning a card over is for. What is back here is what
                        you turned it over to do. */}
                    <h4 className="skill-card__rearname">{card.title}</h4>
                    <span className="skill-card__acts">
                        <button className="skill-card__read" data-stop="" onClick={onRead}>
                            Read it
                        </button>
                        <button className="skill-card__turn" onClick={() => setBack(false)}>
                            Turn back
                        </button>
                    </span>
                </div>
            </div>
        </li>
    );
}
