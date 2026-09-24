"use client";
import { useState } from "react";
import Link from "next/link";
import { CONCEPTS, type ConceptId } from "./education/concepts";
export function ConceptMap() {
    const [selected, setSelected] = useState<ConceptId>("pod");
    const concept = CONCEPTS[selected];
    return (
        <section className="site-concepts" aria-label="How Lemma works">
            <div className="site-chips">
                {Object.values(CONCEPTS).map((c) => (
                    <button
                        key={c.id}
                        aria-pressed={selected === c.id}
                        onClick={() => setSelected(c.id)}
                    >
                        {c.term}
                    </button>
                ))}
            </div>
            <div aria-live="polite">
                <h2>{concept.term}</h2>
                <p>{concept.oneLiner}</p>
                {concept.explainer.map((p) => (
                    <p key={p}>{p}</p>
                ))}
                <blockquote>{concept.example}</blockquote>
                <Link href={"/docs/" + concept.guideSlug}>
                    Read the guide ↗
                </Link>
                <p>
                    Related:{" "}
                    {concept.related.map((id) => (
                        <button key={id} onClick={() => setSelected(id)}>
                            {CONCEPTS[id].term}
                        </button>
                    ))}
                </p>
            </div>
        </section>
    );
}
