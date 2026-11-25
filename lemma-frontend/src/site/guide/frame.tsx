import Link from "next/link";

import { CHAPTERS } from "./contents";

/** Lemma's mark, three rising bars, small enough to sit in a figure. */
export function LemmaBars() {
    return (
        <svg viewBox="0 0 12 12" aria-hidden="true">
            <rect x="1" y="7" width="2.4" height="4" rx="0.6" />
            <rect x="4.8" y="4.5" width="2.4" height="6.5" rx="0.6" />
            <rect x="8.6" y="1.5" width="2.4" height="9.5" rx="0.6" />
        </svg>
    );
}

/** The cover: the title, and a line elevation of a factory with one person in front of it. */
export function GuideCover() {
    return (
        <header className="gb-cover" id="top">
            <div className="gb-g">
                <div className="gb-t">
                    <p className="gb-k">a lemma guide</p>
                    <h1>A <em>factory</em> behind every person</h1>
                    <p className="gb-sub">Why AI still helps one person at a time, what it takes for it to do a team&rsquo;s work, and how Lemma is built around that.</p>
                </div>
                <svg className="gb-hero" viewBox="0 0 460 420" role="img" aria-label="A line elevation of a factory in front of a rising sun, with one person standing in front of it">
                    <circle className="gb-r" cx="250" cy="232" r="150" />
                    <circle className="gb-r" cx="250" cy="232" r="205" />
                    <circle className="gb-r" cx="250" cy="232" r="262" />
                    <circle className="gb-cc" cx="250" cy="232" r="96" />
                    <path className="gb-sk" d="M344 318 L347.5 150 H354.5 L358 318 Z" />
                    <path className="gb-lf" d="M347.2 164 H354.8 M347 172 H355" />
                    <path className="gb-sk" d="M170 318 V262 L200 240 V262 L230 240 V262 L260 240 V262 L290 240 V226 H334 V318 Z" />
                    <path className="gb-lf" d="M170 276 H290 M200 262 V318 M230 262 V318 M260 262 V318 M290 262 V318 M290 240 H334 M301 240 V226 M312 240 V226 M323 240 V226" />
                    <path className="gb-lf" d="M178 290 H192 V306 H178 Z M208 290 H222 V306 H208 Z M238 290 H252 V306 H238 Z M268 290 H282 V306 H268 Z" />
                    <path className="gb-l" d="M304 318 V292 H322 V318" />
                    <path className="gb-l" d="M0 318 H460" />
                    <circle className="gb-l" cx="118" cy="291" r="3.6" />
                    <path className="gb-l" d="M118 294.6 V307 M118 307 L114 318 M118 307 L122 318 M112 300 H124" />
                    <path className="gb-lf" d="M170 336 H358 M170 331 V341 M358 331 V341 M118 336 H170 M118 331 V341" />
                </svg>
            </div>
        </header>
    );
}

/** The whole argument in three paragraphs, then the contents. */
export function GuideIntro() {
    return (
        <section className="gb-s" id="short" aria-label="The short version">
            <div className="gb-g">
                <p className="gb-k gb-lab">the short version</p>
                <div className="gb-c">
                    <p className="gb-lede">Most people use AI the same way: alone, in a chat, one question at a time. Most work is not like that. It passes between people, it comes round again every week, and it has to be right before it goes out.</p>
                    <p>So the answer is not a cleverer chat. It is one place where the work itself lives, the customers and the prices and the drafts and the rules, shared by the whole team and its AI. There the AI can run the parts that repeat, stop to ask before anything that matters, and write down what it is taught where everyone can read it.</p>
                    <p>Software teams already work like this, which is why AI took off there first. We call the arrangement a factory. In Lemma, each one is called a Lemma.</p>
                    <nav aria-label="Chapters">
                        <ol className="gb-toc">
                            {CHAPTERS.map((chapter) => (
                                <li key={chapter.id}>
                                    <a href={`#${chapter.id}`}>
                                        <span className="gb-n">{chapter.n}</span>
                                        <span className="gb-tt">{chapter.title}</span>
                                        <span className="gb-gist">{chapter.gist}</span>
                                    </a>
                                </li>
                            ))}
                        </ol>
                    </nav>
                </div>
            </div>
        </section>
    );
}

/** The end of the guide: where to go next. */
export function GuideFoot() {
    return (
        <footer className="gb-foot">
            <div className="gb-g">
                <p className="gb-k gb-lab">where next</p>
                <ul className="gb-next">
                    <li><Link href="/docs/use/lemmas"><b>Make your first Lemma</b><span>What a Lemma holds, and how to start one.</span></Link></li>
                    <li><Link href="/docs"><b>Read the docs</b><span>Using Lemma, building on it, and running it yourself.</span></Link></li>
                    <li><Link href="/home"><b>Open Lemma</b><span>Sign in, or make an account.</span></Link></li>
                </ul>
            </div>
        </footer>
    );
}
