"use client";

import Image from "next/image";
import { useState } from "react";
import { Bars, Icon, type IconName } from "./icons";
import s from "./landing.module.css";

/** What a teammate's space holds, one part at a time over one big real
 *  screen: pick a part and its screen comes up, with a line on what it is
 *  doing there. All five are one sample teammate's, on the same data. */
const PARTS: { icon: IconName; name: string; sub: string; src: string; alt: string; caption: string }[] = [
    { icon: "agents", name: "Agents", sub: "Act without being asked", src: "/landing/day-agents.webp", alt: "Standing work: what the teammate does on a Slack message, a merged fix and every night.",
      caption: "Standing work: it acts on a Slack message, a merged fix, or every night at 2am." },
    { icon: "workflows", name: "Workflows", sub: "Steps, with people in", src: "/landing/day-workflows.webp", alt: "The Close the loop workflow: its trigger, its runs, and its steps.",
      caption: "Close the loop: the steps the teammate does itself, and the one it hands to the person who shipped the fix." },
    { icon: "apps", name: "Apps", sub: "Screens for the team", src: "/landing/day-apps.webp", alt: "The Feedback loop app, open in the teammate's space.",
      caption: "The Feedback loop board. The PM, the engineer and the account manager each see their own queue." },
    { icon: "tables", name: "Tables", sub: "What everyone shares", src: "/landing/day-tables.webp", alt: "The Feedback Themes table, as a board grouped by stage.",
      caption: "Feedback Themes: the shared record its agents, workflows and apps all work from." },
    { icon: "pages", name: "Pages", sub: "Reports it writes", src: "/landing/day-pages.webp", alt: "The week 40 feedback report, with a live table inside.",
      caption: "The week 40 report, written by the teammate, with a live table inside." },
];

export function Builds() {
    const [on, setOn] = useState(2);
    const part = PARTS[on];
    return (
        <section className={`${s.wrap} ${s.section}`} aria-labelledby="builds-title">
            <div className={s.head} data-quiet="">
                <span className={s.label}><Bars lit={2} />Builds</span>
                <h2 id="builds-title" className={s.title}>It builds what the job needs.</h2>
                <p className={s.body}>Agents, workflows, apps, tables and pages, in one space your whole team can open.</p>
            </div>
            <div className={s.strip} role="tablist" aria-label="Parts of a teammate's space" data-quiet="">
                {PARTS.map((item, index) => (
                    <button key={item.name} type="button" role="tab" className={s.tab} aria-selected={index === on} aria-controls="builds-stage" onClick={() => setOn(index)}>
                        <span className={s.tabName}><Icon name={item.icon} />{item.name}</span>
                        <span className={s.tabSub}>{item.sub}</span>
                    </button>
                ))}
            </div>
            <div id="builds-stage" className={s.stage} role="tabpanel">
                <div className={`${s.shot} ${s.screens}`} data-lamp="">
                    {PARTS.map((item, index) => (
                        <Image key={item.src} src={item.src} width={2880} height={1720} sizes="(max-width: 1240px) 100vw, 1120px" alt={index === on ? item.alt : ""}
                            aria-hidden={index !== on} data-on={index === on ? "" : undefined} />
                    ))}
                </div>
                <p className={s.tabCaption} data-quiet=""><b>{part.name}.</b> {part.caption}</p>
            </div>
        </section>
    );
}
