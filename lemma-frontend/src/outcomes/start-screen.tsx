"use client";

import { useEffect, useRef, useState } from "react";
import { Sk } from "@/ui/skeleton";

/** Starting up, as one screen (design.md, "States").
 *
 *  From the first byte of the app's code to the frame drawn there are four
 *  waits in a row: the code itself, asking who is signed in, putting back what
 *  the device kept, and the organization's first reads. Each used to say so on
 *  a screen of its own, so opening the app flashed four sentences. Now every
 *  one of them draws this: the frame's own shape -- the sidebar on its tint,
 *  a page header and cards -- and no words until it has taken about two
 *  seconds. Each wait mounts it afresh, so the clock lives in the module, not
 *  in a component: the words come two seconds after the first of them, and
 *  a later stage that mounts after that shows them at once rather than
 *  fading them in again.
 *
 *  Self-contained on purpose. It is the fallback while the frame's code
 *  loads, so it imports nothing of the frame's, and its look is in
 *  `start.css`, which the root layout loads before the frame's own sheets. */

/** When words join the shapes. */
export const START_WORDS_AFTER_MS = 2_000;

/** When the first start-up screen of this page appeared; null between them. */
let since: number | null = null;

/** The frame is drawn: the next start-up wait -- after signing in again, say
 *  -- starts its own two seconds. */
export function startingDone(): void {
    since = null;
}

function startedAt(): number {
    since ??= Date.now();
    return since;
}

/** Whether the start-up has lasted long enough for words, on the shared clock. */
function useWordsDue(): { due: boolean; fresh: boolean } {
    /* A stage that mounts once the words are already due shows them still,
       without fading them in a second time. On the server, and on the first
       paint, nothing has started. */
    const [due, setDue] = useState(() => since !== null && Date.now() - since >= START_WORDS_AFTER_MS);
    const [fresh] = useState(() => !due);
    useEffect(() => {
        const left = startedAt() + START_WORDS_AFTER_MS - Date.now();
        const timer = window.setTimeout(() => setDue(true), Math.max(0, left));
        return () => window.clearTimeout(timer);
    }, []);
    return { due, fresh };
}

/** The shimmer, in step from one stage to the next: each stage's shapes are
 *  new elements, and without this the sweep would jump back to its start. */
function useShimmerInStep() {
    const ref = useRef<HTMLDivElement>(null);
    useEffect(() => {
        ref.current?.style.setProperty("--kboot-phase", `-${Math.round(performance.now() % 1500)}ms`);
    }, []);
    return ref;
}

/** The three bars of the logo; rising gently while something is on its way,
 *  still when it is not. */
export function RisingBars({ still = false, big = false }: { still?: boolean; big?: boolean }) {
    return (
        <span className={"krise" + (still ? " krise--still" : "") + (big ? " krise--big" : "")} aria-hidden="true">
            <i /><i /><i />
        </span>
    );
}

const SIDE_PLACES = [70, 82, 52, 90];
const SIDE_LEMMAS = [60, 74, 56];

/** The sidebar's shape on the sidebar's own tint: the logo, the places, a few
 *  Lemmas. Hidden on a phone, where the frame has no sidebar either. */
function SideShape() {
    return (
        <div className="kboot__side" aria-hidden="true">
            <div className="kboot__top">
                <span className="kboot__logo"><i /><i /><i /></span>
                <Sk w={88} />
            </div>
            {SIDE_PLACES.map((width) => (
                <div className="kboot__row" key={width}>
                    <Sk w={18} h={18} r={6} />
                    <Sk w={width} />
                </div>
            ))}
            <div className="kboot__gap" />
            <div className="kboot__label"><Sk w={46} h={7} /></div>
            {SIDE_LEMMAS.map((width) => (
                <div className="kboot__row" key={width}>
                    <Sk w={24} h={24} r={8} />
                    <Sk w={width} />
                </div>
            ))}
            {/* The account's row at the foot, as the sidebar has it. */}
            <div className="kboot__me">
                <Sk w={26} h={26} r={13} />
                <Sk w={64} />
            </div>
        </div>
    );
}

/** A page's shape: its header, then cards. */
function PageShape() {
    return (
        <div className="kboot__page" aria-hidden="true">
            <div className="kboot__head">
                <Sk w={190} h={26} r={8} />
                <Sk w="min(420px, 80%)" />
            </div>
            <div className="kboot__cards">
                {Array.from({ length: 6 }, (_, index) => (
                    <span className="kboot__card" key={index}>
                        <Sk r={14} style={{ aspectRatio: "16 / 10", height: "auto" }} />
                        <Sk w="60%" />
                    </span>
                ))}
            </div>
        </div>
    );
}

/** The one quiet line, low in the page, once it is due. */
function SlowWords({ words }: { words: string }) {
    const { due, fresh } = useWordsDue();
    if (!due) return null;
    return (
        <p className="kboot__note" data-fresh={fresh || undefined} aria-hidden="true">
            <RisingBars />
            {words}…
        </p>
    );
}

/** The API not answering at start: the frame's shape stays, and the page says
 *  so, with the real countdown to the next look and a way to look now. */
export interface StartDown {
    /** Seconds to the next try; null while a try is under way. */
    seconds: number | null;
    onTryNow: () => void;
}

/** The start-up screen. `words` is what the wait is, said only after two
 *  seconds ("Checking you're signed in", "Opening Fjord"); `down` replaces the
 *  page with the API not answering. */
export function StartScreen({ words = "Checking you’re signed in", down }: { words?: string; down?: StartDown }) {
    const ref = useShimmerInStep();
    return (
        <div ref={ref} className="kboot" role={down ? undefined : "status"} aria-label={down ? undefined : words + "…"} aria-busy={down ? undefined : true}>
            <SideShape />
            <div className="kboot__main">
                {down ? <Unreachable down={down} /> : <><PageShape /><SlowWords words={words} /></>}
            </div>
        </div>
    );
}

/** Inside the frame, once it is drawn: a page loading on its own -- another
 *  organization picked, a Lemma being looked up -- in the same shapes, with
 *  its words after two seconds of its own. */
export function StartPage({ words }: { words: string }) {
    const [due, setDue] = useState(false);
    useEffect(() => {
        const timer = window.setTimeout(() => setDue(true), START_WORDS_AFTER_MS);
        return () => window.clearTimeout(timer);
    }, []);
    return (
        <div className="kboot__inpage" role="status" aria-label={words + "…"} aria-busy="true">
            <PageShape />
            {due && <p className="kboot__note" data-fresh aria-hidden="true"><RisingBars />{words}…</p>}
        </div>
    );
}

function Unreachable({ down }: { down: StartDown }) {
    return (
        <div className="kboot__down">
            <div className="kcalm">
                <RisingBars still big />
                {/* The title is announced once; the countdown under it is not
                    read out every second. */}
                <h2 className="kcalm__t" role="alert">Can’t reach Lemma right now</h2>
                <div className="kcalm__line">
                    <p>
                        Its server isn’t answering, so nothing can load yet.{" "}
                        {down.seconds === null
                            ? "Trying again now…"
                            : <>Trying again in <span className="kboot__fig">{down.seconds}</span> s.</>}
                    </p>
                </div>
                <div className="kcalm__acts">
                    <button type="button" className="b b--m b--primary" onClick={down.onTryNow}>Try now</button>
                </div>
            </div>
        </div>
    );
}
