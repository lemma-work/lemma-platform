import Image from "next/image";
import Link from "next/link";
import s from "./landing.module.css";

/* Where each part sits in the capture of Kit's space (public/landing/
   day-space.webp, 1440×820 at 2x): x and y as shares of the picture, so a
   label's line ends on the real thing at any width. Retake the capture and
   these move with it (scripts/capture-landing-shots.mjs prints them). */
const PARTS: { side: "left" | "right"; x: string; y: string; name: string; says: string }[] = [
    { side: "left", x: "4.5%", y: "20.6%", name: "Apps", says: "Screens it builds" },
    { side: "left", x: "4.5%", y: "27.4%", name: "Knowledge", says: "Tables, files, notes" },
    { side: "left", x: "4.5%", y: "34.3%", name: "Workflows", says: "Runs, and waits for people" },
    { side: "right", x: "97%", y: "4.9%", name: "People", says: "Shared, each with a role" },
    { side: "right", x: "51.8%", y: "38.2%", name: "Agents", says: "Does the work itself" },
];

/** Where people reach it from, plugged into the bottom of its space. */
const SURFACES: { name: string; logo?: string }[] = [
    { name: "Slack", logo: "/connector-logos/slack.svg" },
    { name: "Microsoft Teams", logo: "/connector-logos/teams.svg" },
    { name: "WhatsApp", logo: "/connector-logos/whatsapp.svg" },
    { name: "Telegram", logo: "/connector-logos/telegram.svg" },
    { name: "Email" },
    { name: "The app", logo: "/icon.svg" },
];

/** The claim, then the platform itself: one real screen of a teammate's
 *  space, lit from behind, with what it is made of named in the margins and
 *  the places people ask it from plugged in underneath. */
export function Hero() {
    return (
        <section className={s.hero}>
            <div className={s.wrap}>
                <div data-quiet="">
                    <span className={s.pill}><b>Open source</b>Runs on the Claude or ChatGPT plan you already pay for</span>
                    <h1 className={s.headline}>Hire an AI teammate.</h1>
                    <p className={s.intro}>Share it with your people. It learns from all of you and builds itself around how you work.</p>
                    <div className={s.ctas}>
                        <Link className={s.primary} href="/t">Get started free</Link>
                        <Link className={s.secondary} href="/download">Download for Mac</Link>
                    </div>
                </div>

                <figure className={s.space}>
                    <div className={s.art} data-lamp="">
                        <div className={s.contact} aria-hidden="true" />
                        <div className={s.bezel}>
                            <div className={s.frame}>
                                <Image src="/landing/day-space.webp" width={2880} height={1640} priority sizes="(max-width: 1400px) 100vw, 1180px"
                                    alt="A teammate's space: its pages, apps, tables, files and workflows down the side, and a conversation where Dev's correction is sorted and noted." />
                            </div>
                        </div>
                        {PARTS.map(part => (
                            <div key={part.name} className={`${s.note} ${part.side === "left" ? s.left : s.right}`}
                                style={{ "--x": part.x, "--y": part.y } as React.CSSProperties} aria-hidden="true">
                                {part.side === "right" && <><u /><i /></>}
                                <span data-quiet=""><b>{part.name}</b><small>{part.says}</small></span>
                                {part.side === "left" && <><i /><u /></>}
                            </div>
                        ))}
                    </div>
                    <div className={s.plugs}>
                        <figcaption className={s.plugsLabel} data-quiet="">Ask from wherever you already work</figcaption>
                        <div className={s.chips}>
                            {SURFACES.map(surface => (
                                <span key={surface.name} className={s.chip}>
                                    {surface.logo ? <img src={surface.logo} alt="" width={20} height={20} /> : <span aria-hidden="true">@</span>}
                                    {surface.name}
                                </span>
                            ))}
                        </div>
                    </div>
                </figure>
            </div>
        </section>
    );
}
