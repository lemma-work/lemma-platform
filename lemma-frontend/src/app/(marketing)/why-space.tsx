import Link from "next/link";
import { VIGNETTES } from "./vignettes";
import s from "./landing.module.css";
import p from "./product.module.css";

/* Why a teammate needs a space at all, said once on the front door: chat is
   where the asking happens, and the work it produces needs somewhere to stay
   that everybody can open. Then the four places it can stay, each opening
   with the everyday failure it fixes and leading to its own page. Words
   first, in the Trust section's ruled style; the one picture is a vignette
   of four separate asks landing in four places. */

const INTRO = [
    "Most AI at work lives in chat, and by Friday the answer is forty messages up a thread only you can see.",
    "A teammate on Lemma puts the work somewhere: briefs in pages, lists in tables, recurring jobs in workflows, screens in apps. Your people open the same ones it works in.",
];

const PARTS: { slug: string; name: string; line: string; body: string; example: string }[] = [
    {
        slug: "pages", name: "Pages", line: "Documents you write together.",
        body: "Ask from anywhere in a page and it writes there. Charts come from your tables, table views read the rows fresh, and a comment that mentions it comes back as an edit.",
        example: "Team plans launch, a brief listing every asset not yet ready.",
    },
    {
        slug: "tables", name: "Tables", line: "The list everyone works from.",
        body: "Real rows with typed columns. Your teammate moves them as the work moves, your people tick them off, and each person can be kept to their own.",
        example: "Twelve launch assets, each with an owner, a status and a date.",
    },
    {
        slug: "workflows", name: "Workflows", line: "Work that changes hands.",
        body: "The stages of a job, written down once. It starts on its own, hands each step to your teammate, to code or to a named person, and asks them where they already chat.",
        example: "Thursdays at 9 it checks every asset, then waits for Priya’s sign-off.",
    },
    {
        slug: "apps", name: "Apps", line: "A screen built for one job.",
        body: "The internal tool you never had time to build, made by asking. It runs on the same tables, as whoever opens it, so each person sees their part.",
        example: "Launch studio, every asset with its owner and what it still needs.",
    },
];

export function WhySpace() {
    const Moment = VIGNETTES["home-space-fills"];
    return (
        <section className={`${s.wrap} ${s.section}`} aria-labelledby="why-space-title">
            <div className={`${s.head} ${p.whyHead}`}>
                <div>
                    <p className={s.label}>Why it needs a space</p>
                    <h2 id="why-space-title" className={s.title}>You ask in a chat.<br />The work stays<br />in its space.</h2>
                </div>
                <div className={p.whyIntro}>{INTRO.map((para, index) => <p key={index} className={s.body}>{para}</p>)}</div>
            </div>
            {Moment && <figure className={`${p.panel} ${p.whyMoment}`} data-rise=""><div className={p.moment}><Moment /></div><figcaption className={p.caption}>In Kit’s space: four asks in one chat, and where each one ended up.</figcaption></figure>}
            <div className={p.parts}>
                {PARTS.map(part => (
                    <div key={part.slug} data-rise="">
                        <h3>{part.name}</h3>
                        <p className={p.partLine}>{part.line}</p>
                        <p>{part.body}</p>
                        <p className={p.partExample}><span>In Kit’s space:</span> {part.example}</p>
                        <Link className={s.textLink} href={"/product/" + part.slug}>How {part.name.toLowerCase()} work →</Link>
                    </div>
                ))}
            </div>
        </section>
    );
}
