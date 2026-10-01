import Image from "next/image";
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

export function Channels() {
    return (
        <div className={s.wrap}>
            <div className={s.strip}>
                <p className={s.label}>Ask it from wherever your people already talk</p>
                <ul className={s.stripItems}>
                    {CHANNELS.map(channel => (
                        <li key={channel.name}><img src={channel.logo} alt="" width={20} height={20} />{channel.name}</li>
                    ))}
                    <li><span className={s.mail} aria-hidden="true">@</span>Email</li>
                </ul>
            </div>
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

/* ── It builds: four teammates, four apps ─────────────────────────────── */

const APPS: { src: string; width: number; height: number; face: string; app: string; who: string; job: string }[] = [
    { src: "/landing/app-kit.webp", width: 2880, height: 1120, face: "/teammates/loop-v1.png", app: "Launch studio", who: "Kit", job: "every launch asset, its owner and what it still needs" },
    { src: "/landing/app-june.webp", width: 2880, height: 1640, face: "/teammates/frame-v1.png", app: "Customer launchpad", who: "June", job: "a customer’s first import, checked before it runs" },
    { src: "/landing/app-remy.webp", width: 2880, height: 1560, face: "/teammates/pleat-v1.png", app: "Deal desk", who: "Remy", job: "every buyer’s next step, with the reply drafted for review" },
    { src: "/landing/app-scout.webp", width: 2880, height: 1520, face: "/teammates/extended/gem.png", app: "Evidence notebook", who: "Scout", job: "a research question, with the evidence and the doubts beside it" },
];

export function Builds() {
    return (
        <section className={`${s.wrap} ${s.section}`} aria-labelledby="builds-title">
            <div className={s.head}>
                <div><p className={s.label}>It builds</p><h2 id="builds-title" className={s.title}>The tools the job needs, built for it.</h2></div>
                <p className={s.body}>Every teammate makes the screens its work calls for, and everyone involved opens them. Four teammates, four apps.</p>
            </div>
            <div className={s.masonry}>
                {APPS.map(app => (
                    <figure key={app.app} className={s.window}>
                        <figcaption className={s.chrome}>
                            <img src={app.face} alt="" width={22} height={22} />
                            {app.app} <em>· {app.who}</em>
                            <span className={s.dots} aria-hidden="true"><i /><i /><i /></span>
                        </figcaption>
                        <Image src={app.src} width={app.width} height={app.height} sizes="(max-width: 860px) 100vw, 590px"
                            alt={`${app.app}, the app ${app.who} built: ${app.job}.`} />
                    </figure>
                ))}
            </div>
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
                    <Image src="/landing/people.webp" width={1040} height={840} sizes="(max-width: 860px) 100vw, 520px"
                        alt="People with access to Kit: you with full access, Priya who reviews external commitments, and others in the organization who can be added." />
                </div>
                <figcaption className={s.scrawl}>Priya signs off on anything that goes out</figcaption>
            </figure>
        </section>
    );
}

/* ── Correct it once ──────────────────────────────────────────────────── */

const MOMENTS: { label: string; src: string; width: number; height: number; alt: string }[] = [
    { label: "1 · You correct it", src: "/landing/noted.webp", width: 1608, height: 456,
        alt: "Someone tells Kit the readiness check now runs Thursdays at 9. Kit confirms, and a line under its reply says Kit noted this, in Launch checks." },
    { label: "2 · It writes the lesson down", src: "/landing/note-open.webp", width: 1640, height: 420,
        alt: "The Launch checks note: readiness check runs Thursdays at 09:00, moved from Fridays." },
    { label: "3 · Everyone in its space can read it", src: "/landing/remembers.webp", width: 1568, height: 640,
        alt: "What Kit remembers: shared notes on launch checks, publishing and brand voice, with when each last changed." },
];

/** Hand-drawn arrows from each moment to the next, drawn inside the moment
 *  they leave from. */
const ARROWS = [
    <svg key="one" className={`${s.arrow} ${s.arrowOne}`} viewBox="0 0 190 130" fill="none" aria-hidden="true">
        <path d="M8 8 C 40 70, 110 110, 176 116" stroke="#5a3fd4" strokeWidth="2" strokeLinecap="round" />
        <path d="M162 106 L178 116 L162 126" stroke="#5a3fd4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>,
    <svg key="two" className={`${s.arrow} ${s.arrowTwo}`} viewBox="0 0 200 80" fill="none" aria-hidden="true">
        <path d="M190 6 C 160 60, 90 70, 20 60" stroke="#5a3fd4" strokeWidth="2" strokeLinecap="round" />
        <path d="M34 50 L18 60 L32 72" stroke="#5a3fd4" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>,
];

export function Memory() {
    return (
        <section className={`${s.wrap} ${s.section}`} aria-labelledby="memory-title">
            <div className={s.head}>
                <div><p className={s.label}>It remembers</p><h2 id="memory-title" className={s.title}>Correct it once.</h2></div>
                <p className={s.body}>It writes the lesson down where your people can read it, and works that way from then on. Open any note to change it.</p>
            </div>
            <div className={s.storyboard} role="list">
                {MOMENTS.map((moment, index) => (
                    <div key={moment.label} className={s.moment} role="listitem">
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
