import Image from "next/image";
import Link from "next/link";
import { discordUrl, githubUrl } from "@/site/links";
import s from "./landing.module.css";

/* Every screen on this page is a real capture of the sample workspace,
   made by scripts/capture-landing-shots.mjs. Run it again when the product
   changes; never edit or draw the images. */

/* ── Where it answers ─────────────────────────────────────────────────── */

const CHANNELS = [
    { name: "Slack", logo: "/connector-logos/slack.svg" },
    { name: "Teams", logo: "/connector-logos/teams.svg" },
    { name: "WhatsApp", logo: "/connector-logos/whatsapp.svg" },
    { name: "Telegram", logo: "/connector-logos/telegram.svg" },
];

/** Under the hero's buttons, so where it answers is said above the fold. */
export function Channels() {
    return (
        <div className={s.strip}>
            <p className={s.label}>Ask it from wherever your people already talk</p>
            <ul className={s.stripItems}>
                {CHANNELS.map(channel => (
                    <li key={channel.name}><img src={channel.logo} alt="" width={20} height={20} />{channel.name}</li>
                ))}
                <li><span className={s.mail} aria-hidden="true">@</span>Email</li>
            </ul>
        </div>
    );
}

/* ── How it works ─────────────────────────────────────────────────────── */

const STEPS = [
    { face: "loop", title: "Give it a job.", body: "Say what it’s for in a sentence. It asks for what it needs." },
    { face: "cloud", title: "Let your people in.", body: "Add who it works with. Each of them sees their part." },
    { face: "bolt", title: "Correct it once.", body: "It writes the lesson down and works that way from then on." },
];

export function HowItWorks() {
    return (
        <section className={`${s.wrap} ${s.section}`} aria-labelledby="how-title">
            <div className={s.head}>
                <div><p className={s.label}>How it works</p><h2 id="how-title" className={s.title}>Start with one job.</h2></div>
                <p className={s.body}>Most teams begin with the thing somebody does every Monday and would rather not.</p>
            </div>
            <ol className={s.steps}>
                {STEPS.map((step, index) => (
                    <li key={step.title} className={s.step}>
                        <Image className={s.stepFace} src={`/teammates/cast/${step.face}.webp`} width={176} height={176} alt="" />
                        <span className={s.stepNumber} aria-hidden="true">{index + 1}</span>
                        <h3 className={s.stepTitle}>{step.title}</h3>
                        <p className={s.stepBody}>{step.body}</p>
                    </li>
                ))}
            </ol>
        </section>
    );
}

/* ── Everyone asks, each sees their part ──────────────────────────────── */

export function People() {
    return (
        <section className={`${s.wrap} ${s.section} ${s.split}`} aria-labelledby="people-title">
            <div>
                <p className={s.label}>Shared, not copied</p>
                <h2 id="people-title" className={s.title}>Everyone asks. Each sees their part.</h2>
                <p className={s.body} style={{ marginTop: 18 }}>
                    Add the people who need it. It answers each of them within what their role allows, and checks with the
                    right person before it acts.
                </p>
                <ul className={s.facts}>
                    <li>Roles and access per person</li>
                    <li>Approvals where a person should decide</li>
                    <li>One record of what happened</li>
                </ul>
            </div>
            <figure className={s.panel}>
                <div className={s.panelCard}>
                    <Image src="/landing/people.webp" width={1040} height={1036} sizes="(max-width: 860px) 100vw, 520px"
                        alt="People with access to Kit: you with full access, Dev who confirms a fix is live, Sam who answers Enterprise accounts, Alex, and others in the organization who can be added." />
                </div>
                <figcaption className={s.scrawl}>Nobody hears back until Dev says it’s live</figcaption>
            </figure>
        </section>
    );
}

/* ── Correct it once ──────────────────────────────────────────────────── */

const MOMENTS: { label: string; src: string; width: number; height: number; alt: string }[] = [
    { label: "1 · You correct it", src: "/landing/noted.webp", width: 1608, height: 1044,
        alt: "Someone tells Kit that app feels stuck on mobile means the missing push notification, not a timeout. Kit re-sorts the 14 reports, and a line under its reply says Kit noted this, in Feedback rules." },
    { label: "2 · It writes the lesson down", src: "/landing/note-open.webp", width: 1640, height: 420,
        alt: "The Feedback rules note: on mobile, app feels stuck is the missing push notification, not a timeout, from Dev; timeouts go to Dev, top priority." },
    { label: "3 · Everyone in its space can read it", src: "/landing/remembers.webp", width: 1568, height: 640,
        alt: "What Kit remembers: shared notes on feedback rules, closing the loop and capture, with when each last changed." },
];

/** Hand-drawn arrows from each moment to the next, drawn inside the moment
 *  they leave from. */
const ARROWS = [
    <svg key="one" className={`${s.arrow} ${s.arrowOne}`} viewBox="0 0 190 130" fill="none" aria-hidden="true">
        <path pathLength={1} d="M8 8 C 40 70, 110 110, 176 116" stroke="#5a3fd4" strokeWidth="2" strokeLinecap="round" />
        <path pathLength={1} d="M162 106 L178 116 L162 126" stroke="#5a3fd4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>,
    <svg key="two" className={`${s.arrow} ${s.arrowTwo}`} viewBox="0 0 200 80" fill="none" aria-hidden="true">
        <path pathLength={1} d="M190 6 C 160 60, 90 70, 20 60" stroke="#5a3fd4" strokeWidth="2" strokeLinecap="round" />
        <path pathLength={1} d="M34 50 L18 60 L32 72" stroke="#5a3fd4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>,
];

export function Memory() {
    return (
        <section className={`${s.wrap} ${s.section}`} aria-labelledby="memory-title">
            <div className={s.head}>
                <div><p className={s.label}>It remembers</p><h2 id="memory-title" className={s.title}>Correct it once.</h2></div>
                <p className={s.body}>It writes the lesson down where your people can read it, and works that way from then on. Open any note to change it.<br /><Link className={`${s.textLink} ${s.moreLink}`} href="/product/memory">How memory works →</Link></p>
            </div>
            <div className={s.storyboard} role="list">
                {MOMENTS.map((moment, index) => (
                    <div key={moment.label} className={s.moment} role="listitem" data-rise="">
                        <span className={s.label}>{moment.label}</span>
                        <div className={s.momentCard}>
                            <Image src={moment.src} width={moment.width} height={moment.height} sizes="(max-width: 1080px) 100vw, 560px" alt={moment.alt} />
                        </div>
                        {ARROWS[index]}
                    </div>
                ))}
            </div>
        </section>
    );
}

/* ── Trust ────────────────────────────────────────────────────────────── */

const TRUST = [
    { title: "Permissions that hold", body: "Roles per person, access per row, approvals where a person decides. The same rules in the app, in Slack and on WhatsApp." },
    { title: "Your model, or ours", body: "Hosted models, your own provider key, or the coding agent already on your machine: Claude Code, Codex, Cursor." },
    { title: "Open source", body: "AGPLv3 core, Apache 2.0 SDKs. Read how it works, and run it yourself when you need to." },
];

export function Trust() {
    return (
        <section className={`${s.wrap} ${s.section}`} aria-labelledby="trust-title">
            <p className={s.label}>Trust</p>
            <h2 id="trust-title" className={s.title}>Built for real work.</h2>
            <div className={s.trust}>
                {TRUST.map(item => (
                    <div key={item.title}><h3>{item.title}</h3><p>{item.body}</p></div>
                ))}
            </div>
        </section>
    );
}

/* ── Open source ──────────────────────────────────────────────────────── */

export function OpenSource() {
    return (
        <section className={`${s.wrap} ${s.section} ${s.oss}`} id="open-source" aria-labelledby="oss-title">
            <div>
                <p className={s.label}>Open source, from the start</p>
                <h2 id="oss-title" className={s.title}>Your work.<br />An open foundation.</h2>
                <p className={s.body} style={{ marginTop: 18 }}>See how Lemma works. Run it on your own infrastructure. Help shape what it becomes.</p>
                <div className={s.ossLinks}>
                    <a href={githubUrl} target="_blank" rel="noreferrer">Explore the code on GitHub ↗</a>
                    <a href="/docs/getting-started">Self-host Lemma ↗</a>
                    <a href={githubUrl + "/blob/main/CONTRIBUTING.md"} target="_blank" rel="noreferrer">Start contributing ↗</a>
                    <a href={discordUrl} target="_blank" rel="noreferrer">Talk to us on Discord ↗</a>
                </div>
                <small className={s.license}>AGPLv3 core · Apache 2.0 SDKs</small>
            </div>
            <Image src="/images/open-source-3d.png" width={1536} height={1024} sizes="(max-width: 1080px) 100vw, 600px"
                alt="Lemma's characters building an open-source mark together." />
        </section>
    );
}
