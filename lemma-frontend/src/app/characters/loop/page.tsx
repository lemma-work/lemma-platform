"use client";
import { ALL_CAST } from "../extended-cast";
import { useState } from "react";
import dynamic from "next/dynamic";
import type { Mood } from "./loop-puppet";
import styles from "./page.module.css";
const LoopScene = dynamic(() => import("./loop-puppet").then(m => m.LoopPuppet), { ssr: false, loading: () => <div className={styles.loading}>Meeting Loop…</div> });
const STATES: {id: Mood; name: string; caption: string}[] = [
    {id:"delighted",name:"Delighted",caption:"Smiling eyes, open arms. A little joy, then space to settle."},
    {id:"idle",name:"At ease",caption:"Move nearby. His eyes notice you before his body follows."},
    {id:"listening",name:"Listening",caption:"A little lean, steady attention. Space for you to speak."},
    {id:"thinking",name:"Thinking",caption:"A glance away to think. Come closer and he notices you again."},
    {id:"waiting",name:"Needs you",caption:"One raised hand. Then he waits — no repeated waving."},
];
export default function LoopStudy() {
    const [mood,setMood] = useState<Mood>("idle"); const [greeting,setGreeting] = useState(0); const [paused,setPaused] = useState(false);
    return <main className={styles.page}>
        <header><a href="/characters">← The cast</a><span>LEMMA / LAYERED CHARACTER · 03</span><button onClick={()=>setPaused(!paused)} aria-pressed={paused}>{paused ? "Resume motion" : "Pause motion"}</button></header>
        <section className={styles.study}><div className={styles.copy}><p>A LITTLE PRESENCE</p><h1>Oh. <br/>Hi, you.</h1><div>Meet Loop again.<br/>This time, he’s paying attention.</div><button className={styles.hello} onClick={()=>setGreeting(greeting+1)}>Say hello <span>↗</span></button><small>Try moving around him, then stop.<br/>On touch, say hello or choose a pose.</small></div>
        <div className={styles.stage}><div className={styles.halo}/><LoopScene mood={mood} greeting={greeting} paused={paused}/><span className={styles.shadow}/><div className={styles.caption} aria-live="polite">{STATES.find(s=>s.id===mood)?.caption}</div></div></section>
        <nav className={styles.states} aria-label="Preview a behavior">{STATES.map(s=><button key={s.id} aria-pressed={mood===s.id} onClick={()=>setMood(s.id)}><span/>{s.name}</button>)}</nav>
        <nav className={styles.castLinks} aria-label="Meet the cast">{ALL_CAST.map(name => <a key={name} href={`/characters/${name}`} aria-current={name === "loop" ? "page" : undefined}>{name[0].toUpperCase() + name.slice(1)} ↗</a>)}</nav>
        <footer>Interactive layered character · Poses are simulated · No microphone or live agent connection</footer>
    </main>;
}
