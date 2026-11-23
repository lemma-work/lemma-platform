import Link from "next/link";
import { CaretRight, Code, Robot, Users } from "@phosphor-icons/react/dist/ssr";
import { docsPageMap } from "@/site/data/docs";
import {
    USE_LEMMA_TOPICS,
    docsNav,
    quickstartSteps,
} from "@/site/docs-content";
import { DocsBigSearch, DocsPhoneBar } from "@/site/docs-shell";

/** Where a builder starts. */
const BUILD_START = [
    "getting-started",
    "sdk/client",
    "cli/overview",
    "guides/build-a-app",
];

/* The docs home: search, the two halves of the docs (one of them still
   being written), three first steps, and every page for builders. */
export function DocsHome() {
    const start = BUILD_START.flatMap((slug) => docsPageMap.get(slug) ?? []);
    const steps = quickstartSteps();
    return (
        <main id="content" className="docs-main docs-main--home">
            <DocsPhoneBar />
            <h1 className="docs-h1">Documentation</h1>
            <p className="docs-lede">
                How Lemma works, and how to build on it: the CLI, the SDK, and
                the platform under them.
            </p>
            <DocsBigSearch />
            <div className="docs-doors">
                <section className="docs-door" data-soon aria-labelledby="door-use">
                    <span className="docs-door__i">
                        <Users aria-hidden="true" />
                    </span>
                    <span className="docs-tag">Being written</span>
                    <h2 id="door-use">Use Lemma</h2>
                    <p>
                        For everyone working with Lemmas: making one, giving it
                        apps and automations, and where people reach it.
                    </p>
                    <ul>
                        {USE_LEMMA_TOPICS.map((topic) => (
                            <li key={topic}>
                                <CaretRight aria-hidden="true" />
                                {topic}
                            </li>
                        ))}
                    </ul>
                </section>
                <section className="docs-door" aria-labelledby="door-build">
                    <span className="docs-door__i">
                        <Code aria-hidden="true" />
                    </span>
                    <h2 id="door-build">Build on Lemma</h2>
                    <p>
                        For developers: the CLI, the SDK for apps and functions,
                        and how the platform&rsquo;s pieces fit.
                    </p>
                    <ul>
                        {start.map((page) => (
                            <li key={page.slug}>
                                <Link href={"/docs/" + page.slug}>
                                    <CaretRight aria-hidden="true" />
                                    {page.title}
                                </Link>
                            </li>
                        ))}
                    </ul>
                </section>
            </div>
            {steps.length > 0 && (
                <>
                    <h2 className="docs-sec-h">Start here</h2>
                    <ol className="docs-steps">
                        {steps.map((step) => (
                            <li key={step.title}>
                                <Link href={step.href}>{step.title}</Link>
                                <code>{step.command}</code>
                            </li>
                        ))}
                    </ol>
                </>
            )}
            <h2 className="docs-sec-h">Everything for builders</h2>
            <div className="docs-groups">
                {docsNav().map((group) => (
                    <section key={group.title}>
                        <h3>{group.title}</h3>
                        <ul>
                            {group.pages.map((page) => (
                                <li key={page.slug}>
                                    <Link href={"/docs/" + page.slug}>
                                        {page.title}
                                    </Link>
                                </li>
                            ))}
                        </ul>
                    </section>
                ))}
            </div>
            <p className="docs-agents">
                <Robot aria-hidden="true" />
                <span>
                    Handing these to an agent? Every page copies as Markdown,
                    and <a href="/llms.txt"><code>/llms.txt</code></a> tells it
                    where to start.
                </span>
            </p>
        </main>
    );
}
