"use client";
import { useState } from "react";
import Link from "next/link";
import { CONCEPTS } from "./education/concepts";
export function Help() {
    const [query, setQuery] = useState("");
    return (
        <section>
            <h2>Learn Lemma</h2>
            <p>
                Start with an ongoing responsibility, then give your teammate
                the tools and context it needs.
            </p>
            <p>
                <Link href="/docs/guides/first-agent" target="_blank">
                    Your first teammate ↗
                </Link>{" "}
                ·{" "}
                <Link href="/docs/how-lemma-works" target="_blank">
                    How Lemma works ↗
                </Link>{" "}
                ·{" "}
                <Link href="/templates" target="_blank">
                    Templates ↗
                </Link>
            </p>
            <label>
                Find a concept
                <input
                    className="input"
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                />
            </label>
            <div className="site-grid">
                {Object.values(CONCEPTS)
                    .filter((c) =>
                        (c.term + " " + c.oneLiner)
                            .toLowerCase()
                            .includes(query.toLowerCase()),
                    )
                    .map((c) => (
                        <Link
                            className="site-card"
                            href={"/docs/" + c.guideSlug}
                            key={c.id}
                            target="_blank"
                        >
                            <h3>{c.term}</h3>
                            <p>{c.oneLiner}</p>
                            <span>Read the guide ↗</span>
                        </Link>
                    ))}
            </div>
        </section>
    );
}
