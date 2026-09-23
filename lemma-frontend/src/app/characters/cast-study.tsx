"use client";

import { ALL_CAST } from "./extended-cast";
import { useState } from "react";
import { CastPuppet, type CastName } from "./cast-puppet";
import type { Mood } from "./loop/loop-puppet";
import styles from "./loop/page.module.css";

const DETAILS = {
    pleat: { name: "Pleat", heading: <>Well, <br/>hello there.</>, intro: "A thoughtful little soul.\nWith a warm hello just for you.", joy: "A gentle wave, smiling eyes, and a little lift of the toe.", halo: "radial-gradient(circle at 45% 40%,#fcf7ff,#e5d7ef 65%,#f4efe600 72%)" },
    frame: { name: "Frame", heading: <>Hey! <br/>Look at you.</>, intro: "Curious by nature.\nAlways happy to see you.", joy: "A bright little wave, a playful lean, then back to you.", halo: "radial-gradient(circle at 45% 40%,#f7faff,#d5e0f2 65%,#f4efe600 72%)" },
};
const STATES: { id: Mood; name: string; caption: string }[] = [
    { id: "delighted", name: "Delighted", caption: "" },
    { id: "idle", name: "At ease", caption: "Move nearby. The eyes notice you before the body follows." },
    { id: "listening", name: "Listening", caption: "A little lean, steady attention. All ears." },
    { id: "thinking", name: "Thinking", caption: "A glance away to think. Move closer to catch their eye." },
    { id: "waiting", name: "Needs you", caption: "A quietly raised hand. Take your time." },
];

export function CastStudy({ character }: { character: CastName }) {
    const details = DETAILS[character];
    const [mood, setMood] = useState<Mood>("idle");
    const [greeting, setGreeting] = useState(0), [paused, setPaused] = useState(false);
    return <main className={styles.page}>
        <header><a href="/characters">← The cast</a><span>LEMMA / MEET {details.name.toUpperCase()}</span><button onClick={() => setPaused(!paused)} aria-pressed={paused}>{paused ? "Resume motion" : "Pause motion"}</button></header>
        <section className={styles.study}>
            <div className={styles.copy}><p>A LITTLE PRESENCE</p><h1>{details.heading}</h1><div style={{ whiteSpace: "pre-line" }}>{details.intro}</div><button className={styles.hello} onClick={() => setGreeting(value => value + 1)}>Say hello <span>↗</span></button><small>Try moving around, then stop.<br/>On touch, say hello or choose a pose.</small></div>
            <div className={styles.stage}><div className={styles.halo} style={{ background: details.halo }}/><CastPuppet character={character} mood={mood} greeting={greeting} paused={paused}/><span className={styles.shadow}/><div className={styles.caption} aria-live="polite">{mood === "delighted" ? details.joy : STATES.find(state => state.id === mood)?.caption}</div></div>
        </section>
        <nav className={styles.states} aria-label="Preview a behavior">{STATES.map(state => <button key={state.id} aria-pressed={mood === state.id} onClick={() => setMood(state.id)}><span/>{state.name}</button>)}</nav>
        <nav className={styles.castLinks} aria-label="Meet the cast">{ALL_CAST.map(name => <a key={name} href={`/characters/${name}`} aria-current={character === name ? "page" : undefined}>{name[0].toUpperCase() + name.slice(1)} <span>↗</span></a>)}</nav>
        <footer>Interactive layered character · Poses are simulated · No microphone or live agent connection</footer>
    </main>;
}
