"use client";
import { useState } from "react";
import type { Mood } from "./loop/loop-puppet";
import { ExtendedPuppet } from "./extended-puppet";
import { ALL_CAST, EXTENDED, type NewCharacter } from "./extended-cast";
import styles from "./loop/page.module.css";

const STATES: { id: Mood; label: string; caption: string }[] = [
    { id: "idle", label: "At ease", caption: "Come closer. The eyes notice you before the body follows." },
    { id: "delighted", label: "Delighted", caption: "A smile and an open stance. Happy you’re here." },
    { id: "listening", label: "Listening", caption: "A little lean. You have their attention." },
    { id: "thinking", label: "Thinking", caption: "A thoughtful glance away, then back to you." },
    { id: "waiting", label: "Needs you", caption: "One raised hand. No rush." },
];
export function ExtendedStudy({ character }: { character: NewCharacter }) {
    const details = EXTENDED[character];
    const [mood, setMood] = useState<Mood>("idle");
    const [greeting, setGreeting] = useState(0), [paused, setPaused] = useState(false);
    return <main className={styles.page}>
        <header><a href="/characters">← The cast</a><span>LEMMA / MEET {details.name.toUpperCase()}</span><button aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? "Resume motion" : "Pause motion"}</button></header>
        <section className={styles.study}>
            <div className={styles.copy}><p>A LITTLE PRESENCE</p><h1 style={{ fontSize: "clamp(60px, 7vw, 100px)" }}>{details.heading}</h1><div>{details.intro}</div><button className={styles.hello} onClick={() => setGreeting(value => value + 1)}>Say hello <span>↗</span></button><small>{details.greeting}<br/>Move nearby, then stop. See who notices.</small></div>
            <div className={styles.stage}><div className={styles.halo} style={{ background: `radial-gradient(circle,#fffaf5,${details.color}24 65%,transparent 72%)` }}/><ExtendedPuppet character={character} mood={mood} greeting={greeting} paused={paused}/><span className={styles.shadow}/><div className={styles.caption} aria-live="polite">{STATES.find(state => state.id === mood)?.caption}</div></div>
        </section>
        <nav className={styles.states} aria-label="Preview a behavior">{STATES.map(state => <button key={state.id} aria-pressed={mood === state.id} onClick={() => setMood(state.id)}><span/>{state.label}</button>)}</nav>
        <nav className={styles.castLinks} aria-label="Meet the cast">{ALL_CAST.map(name => <a key={name} href={`/characters/${name}`} aria-current={character === name ? "page" : undefined}>{name[0].toUpperCase() + name.slice(1)} ↗</a>)}</nav>
        <footer>Interactive layered character · Poses are simulated · No live agent connection</footer>
    </main>;
}
