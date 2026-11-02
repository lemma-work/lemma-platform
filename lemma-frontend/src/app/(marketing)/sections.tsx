import Image from "next/image";
import Link from "next/link";
import { githubUrl } from "@/site/links";
import { Bars, Icon, type IconName } from "./icons";
import s from "./landing.module.css";

/** One teammate, everyone's: who is in and what each does, beside the rules
 *  it learned, each with the name of whoever taught it. */
export function Learns() {
    return (
        <section className={`${s.wrap} ${s.section}`} aria-labelledby="learns-title">
            <div className={s.head} data-quiet="">
                <span className={s.label}><Bars lit={1} />Shared</span>
                <h2 id="learns-title" className={s.title}>Learns from all of you.</h2>
                <p className={s.body}>Everyone works with the same teammate, each in their own role. When anyone corrects it, it writes the rule down with their name, and works that way for everyone.</p>
            </div>
            <div className={s.pair}>
                <figure data-rise="">
                    <div className={`${s.shot} ${s.dialog}`} data-lamp="">
                        <Image src="/landing/day-people.webp" width={1040} height={1036} sizes="(max-width: 960px) 100vw, 480px" alt="People with access to the teammate: you, Dev, Sam and Alex, each with what they do." />
                    </div>
                    <figcaption data-quiet="">Who&rsquo;s in, and what each person does</figcaption>
                </figure>
                <figure data-rise="">
                    <div className={s.shot}>
                        <Image src="/landing/day-rules.webp" width={1520} height={510} sizes="(max-width: 960px) 100vw, 640px" alt="The feedback rules it learned, each marked with who taught it: Dev or You." />
                    </div>
                    <figcaption data-quiet="">What it learned, and from whom</figcaption>
                </figure>
            </div>
        </section>
    );
}

const FACTS: { icon: IconName; name: string; says: string }[] = [
    { icon: "plan", name: "Your AI plan", says: "Runs on the Claude or ChatGPT plan you already pay for, or any API key." },
    { icon: "place", name: "Where you want it", says: "Lemma Cloud, your Mac, or your own server." },
    { icon: "seen", name: "Nothing hidden", says: "Every step it took, and everything it looked at." },
    { icon: "kept", name: "Yours to keep", says: "A teammate exports as a folder of files. Open source." },
];

export function Yours() {
    return (
        <section className={`${s.wrap} ${s.section}`} aria-labelledby="yours-title">
            <div className={s.head} data-quiet="">
                <span className={s.label}><Bars lit={3} />Yours</span>
                <h2 id="yours-title" className={s.title}>On your terms.</h2>
            </div>
            <div className={s.facts} data-quiet="">
                {FACTS.map(fact => (
                    <div key={fact.name}>
                        <span className={s.factName}><Icon name={fact.icon} />{fact.name}</span>
                        <p>{fact.says}</p>
                    </div>
                ))}
            </div>
            <p className={s.devLine} data-quiet="">
                Got a developer? Claude Code or Codex can build a whole teammate from one prompt.
                <a href={githubUrl} target="_blank" rel="noreferrer">See how on GitHub →</a>
            </p>
        </section>
    );
}

/** One way in, under the logo's bars rising into the light. */
export function Close() {
    return (
        <section className={s.close} aria-labelledby="close-title">
            <div className={s.wrap} data-quiet="">
                <div className={s.bars} data-rise="" data-lamp="" aria-hidden="true"><i /><i /><i /></div>
                <h2 id="close-title">Hire your first teammate.</h2>
                <div className={s.ctas}>
                    <Link className={s.primary} href="/t">Get started free</Link>
                    <Link className={s.secondary} href="/download">Download for Mac</Link>
                </div>
            </div>
        </section>
    );
}
