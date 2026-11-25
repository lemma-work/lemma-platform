import Link from "next/link";
import {
    CaretRight,
    Code,
    HardDrives,
    Robot,
    Users,
} from "@phosphor-icons/react/dist/ssr";
import { docsPageMap } from "@/site/data/docs";
import { runItYourselfPage, useLemmaPages } from "@/site/data/use-docs";
import { docsNavFor, quickstartSteps } from "@/site/docs-content";
import { DocsBigSearch, DocsPhoneBar } from "@/site/docs-shell";

/** Where a builder starts. */
const BUILD_START = [
    "getting-started",
    "sdk/client",
    "cli/overview",
    "guides/build-a-app",
];

/* The docs home: search, the guide, the two halves of the docs, where Lemma
   runs, three first steps, and every page for builders. */
export function DocsHome() {
    const start = BUILD_START.flatMap((slug) => docsPageMap.get(slug) ?? []);
    const steps = quickstartSteps();
    return (
        <main id="content" className="docs-main docs-main--home">
            <DocsPhoneBar />
            <h1 className="docs-h1">Documentation</h1>
            <p className="docs-lede">
                How to use Lemma, how to build on it, and how to run it
                yourself.
            </p>
            <DocsBigSearch />
            <Link className="docs-guide" href="/guide">
                <span className="docs-tag">The guide</span>
                <span className="docs-guide__t">A factory behind every person</span>
                <span className="docs-guide__d">
                    What we believe about working with AI, and why Lemma is
                    built the way it is. Start here if you are new.
                </span>
                <CaretRight aria-hidden="true" />
            </Link>
            <div className="docs-doors">
                <section className="docs-door" aria-labelledby="door-use">
                    <span className="docs-door__i">
                        <Users aria-hidden="true" />
                    </span>
                    <h2 id="door-use">Use Lemma</h2>
                    <p>
                        For everyone working with Lemmas: making one, giving it
                        apps and automations, and where people reach it.
                    </p>
                    <ul>
                        {useLemmaPages.map((page) => (
                            <li key={page.slug}>
                                <Link href={"/docs/" + page.slug}>
                                    <CaretRight aria-hidden="true" />
                                    {page.title}
                                </Link>
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
            <Link
                className="docs-run"
                href={"/docs/" + runItYourselfPage.slug}
            >
                <span className="docs-door__i">
                    <HardDrives aria-hidden="true" />
                </span>
                <span className="docs-run__t">{runItYourselfPage.title}</span>
                <span className="docs-run__d">
                    Lemma Cloud, the Mac app in Local mode, or your own
                    server.
                </span>
                <CaretRight aria-hidden="true" />
            </Link>
            {steps.length > 0 && (
                <>
                    <h2 className="docs-sec-h">Start building</h2>
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
                {docsNavFor("build").map((group) => (
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
