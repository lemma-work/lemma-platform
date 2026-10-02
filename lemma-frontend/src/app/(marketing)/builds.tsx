"use client";

import Image from "next/image";
import { useState } from "react";
import s from "./landing.module.css";

/* ── It builds: a few teammates and their apps ─────────────────────────────────
   One app at a time, large enough to read, with a tab for each teammate.
   Every screen is a real capture of the sample workspace, all the same size
   so switching never moves the page (scripts/capture-landing-shots.mjs). */

const APPS: { id: string; src: string; face: string; app: string; who: string; job: string }[] = [
    { id: "kit", src: "/landing/app-kit.webp", face: "/teammates/loop-v1.png", app: "Feedback loop", who: "Kit", job: "Every user report, the fix it waits on, and who hears back." },
    { id: "remy", src: "/landing/app-remy.webp", face: "/teammates/pleat-v1.png", app: "Deal desk", who: "Remy", job: "Every buyer’s next step, with the reply drafted for review." },
    { id: "june", src: "/landing/app-june.webp", face: "/teammates/frame-v1.png", app: "Customer launchpad", who: "June", job: "A customer’s first import, checked before it runs." },
    { id: "scout", src: "/landing/app-scout.webp", face: "/teammates/extended/gem.png", app: "Evidence notebook", who: "Scout", job: "A research question, with the evidence and the doubts beside it." },
];

export function Builds() {
    const [shown, setShown] = useState(APPS[0].id);
    return (
        <section className={`${s.wrap} ${s.section}`} aria-labelledby="builds-title">
            <div className={s.head}>
                <div><p className={s.label}>It builds</p><h2 id="builds-title" className={s.title}>The tools the job needs, built for it.</h2></div>
                <p className={s.body}>Every teammate makes the screens its work calls for, and everyone involved opens them.</p>
            </div>
            <div className={s.appTabs} role="tablist" aria-label="Apps teammates built">
                {APPS.map(app => (
                    <button key={app.id} id={"app-tab-" + app.id} type="button" role="tab" aria-selected={app.id === shown} aria-controls={"app-" + app.id}
                        className={s.appTab} onClick={() => setShown(app.id)}>
                        <img src={app.face} alt="" width={32} height={32} />
                        <span><b>{app.app}</b><em>{app.who}</em><small>{app.job}</small></span>
                    </button>
                ))}
            </div>
            <div className={s.appFrame}>
                {APPS.map(app => (
                    <div key={app.id} id={"app-" + app.id} role="tabpanel" aria-labelledby={"app-tab-" + app.id} hidden={app.id !== shown}>
                        <Image src={app.src} width={2560} height={1440} sizes="(max-width: 1240px) 100vw, 1200px"
                            alt={`${app.app}, the app ${app.who} built: ${app.job}`} />
                    </div>
                ))}
            </div>
        </section>
    );
}
