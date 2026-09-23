"use client";

import { ExtendedPuppet } from "./extended-puppet";
import { NEW_CAST, EXTENDED } from "./extended-cast";
import { useState } from "react";
import { Mark } from "@/shell/mark";
import { characterUrl } from "@/shell/character";
import styles from "./page.module.css";

/** The identity preview above is about these three specifically — the shapes
 *  the app shipped with. The cast is twenty-four now; the rest are below. */
const FEATURED = ["loop", "pleat", "frame"] as const;
type Featured = typeof FEATURED[number];

const DETAILS = {
    loop: { title: "Loop", job: "Keeps things moving.", role: "Follow-ups & coordination", color: "#f2a47c", text: "A warm, outgoing presence for the teammate that remembers the next step." },
    pleat: { title: "Pleat", job: "Holds the whole picture.", role: "Research & synthesis", color: "#c8abe8", text: "Thoughtful and composed, with a silhouette you can pick out at a glance." },
    frame: { title: "Frame", job: "Makes ideas tangible.", role: "Design & creative work", color: "#8caff4", text: "An open, curious character for the teammate that helps you see something new." },
};

export default function Characters() {
    const [selected, setSelected] = useState<Featured>("loop");
    const [dark, setDark] = useState(false);
    const detail = DETAILS[selected];
    const mark = (name: Featured, size: number) => <Mark seed={name} name={DETAILS[name].title} icon={characterUrl(name)} size={size} />;
    return <main className={styles.page} data-dark={dark}>
        <header className={styles.header}><a href="/t">lemma<span> / character study</span></a><button onClick={() => setDark(!dark)}>{dark ? "Light" : "Dark"} background</button></header>
        <div className={styles.intro}><p>TEAMMATE IDENTITIES · PROTOTYPE 01</p><h1>A little more someone.</h1><div>Three familiar shapes, with a life of their own. <a href={`/characters/${selected}`}>Meet the interactive {detail.title} →</a></div></div>
        <section className={styles.workspace} aria-label="Teammate identity preview">
            <aside className={styles.sidebar}><p>YOUR TEAMMATES</p>{FEATURED.map(name => <button key={name} aria-pressed={selected === name} onClick={() => setSelected(name)}>{mark(name, 40)}<span>{DETAILS[name].title}<small>{DETAILS[name].role.split(" &")[0]}</small></span></button>)}<div className={styles.note}>Select a teammate.<br />Hover over their portrait.</div></aside>
            <div className={styles.profile}>
                <div className={styles.profilebar}>{mark(selected, 28)}<span>{detail.title}</span><span>Profile</span><small>Sample identity</small></div>
                <div className={styles.profilebody}>
                    <div className={styles.figure} style={{ background: detail.color }}>{mark(selected, 280)}<span>{detail.title.toUpperCase()} / 0{FEATURED.indexOf(selected) + 1}</span></div>
                    <div className={styles.bio}><p>MEET {detail.title.toUpperCase()}</p><h2>{detail.job}</h2><p>{detail.text}</p><div className={styles.sizes}><span>At actual size</span>{[28, 36, 48, 64].map(size => <div key={size}>{mark(selected, size)}<small>{size}px</small></div>)}</div><div className={styles.message}>{mark(selected, 32)}<div><strong>{detail.title}</strong><p>Ready when you are.</p></div></div></div>
                </div>
            </div>
        </section>
        <section className={styles.cast} aria-label="Choose a character">{FEATURED.map(name => <button key={name} onClick={() => setSelected(name)} aria-pressed={selected === name}>{mark(name, 140)}<span>{DETAILS[name].title}</span><small>{DETAILS[name].role}</small></button>)}</section>
        <section className={styles.newCast} aria-label="Meet twenty-one more characters"><h2>Twenty-one more little someones.</h2><div>{NEW_CAST.map(name => <a key={name} href={`/characters/${name}`}><div className={styles.miniCharacter}><ExtendedPuppet character={name} mood="idle" greeting={0} paused={false} pixels={210}/></div><strong>{EXTENDED[name].name} ↗</strong><span>{EXTENDED[name].intro}</span></a>)}</div></section>
        <footer className={styles.footer}>Character studies · Open a teammate to try their greeting · No live activity implied</footer>
    </main>;
}
