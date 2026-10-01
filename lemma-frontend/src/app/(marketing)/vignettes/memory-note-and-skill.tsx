import type { CSSProperties, ReactNode } from "react";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./memory-note-and-skill.module.css";

/* What the New skill card puts in the message box, word for word as the
   About page asks for it (space/about-page.tsx). */
const REQUEST = "Write me a new skill. Use the lemma-skill-creator skill: decide its triggers, write the instructions, and publish it under /skills. Ask me what it should do first.";
const STEPS = [
    "Start from the approved security overview.",
    "Answer only what was asked.",
    "Anything the overview doesn’t cover goes to Priya.",
];

/* The line marks a card wears, as skills-view.tsx draws them; each skill's
   is picked by a hash of its folder name. */
const GRID = (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
        <rect x="4" y="4" width="7" height="7" rx="1" /><rect x="13" y="4" width="7" height="7" rx="1" />
        <rect x="4" y="13" width="7" height="7" rx="1" /><circle cx="16.5" cy="16.5" r="3.5" />
    </svg>
);
const STACK = (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
        <path d="M12 3 21 8l-9 5-9-5Z" /><path d="M3 12.5 12 17.5 21 12.5" />
    </svg>
);
const TURN = (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
        <path d="M13 8a5 5 0 1 1-1.6-3.7" /><path d="M13 2.6v2.6h-2.6" />
    </svg>
);

function Front({ name, line, words, glyph }: { name: string; line: string; words: number; glyph: ReactNode }) {
    return (
        <div className={v.face}>
            <span className={v.band} />
            <span className={v.eyebrow}><i>Skill</i>{glyph}</span>
            <span className={v.name}>{name}</span>
            <span className={v.line}>{line}</span>
            <span className={v.foot}><em>{words} words</em><i>{TURN}turn</i></span>
        </div>
    );
}

/** A skill, written on request, beside a note: on Remy's About page you
 *  click New skill, the request it fills in is sent, and a Security
 *  questionnaire card joins what Remy has been taught. Turned over and read,
 *  it is a whole procedure; the note under it is still one line.
 *
 *  Beats: 1 the pointer on New skill; 2 the request in the message box;
 *  3 sent; 4 the new card slides into the row; 5 it turns over; 6 Read it
 *  opens its SKILL.md in place of the row, above the one-line note.
 *
 *  On a phone it shows New skill, Brand voice and where the SKILL.md opens,
 *  and stops short of the new card, which would only show as an edge. */
export function MemoryNoteAndSkill() {
    return (
        <Vignette className={v.root} width={640} height={480} beats={[900, 1000, 1900, 1600, 1200, 1600]} hold={3600} phone={{ x: 12, width: 350 }}
            label="On Remy’s About page, someone clicks New skill under what Remy has been taught. A request to write a new skill, asking what it should do first, fills the message box and is sent. A Security questionnaire skill card joins the row, turns over, and Read it opens its SKILL.md: three numbered steps for answering a buyer’s security questionnaire, above the one-line note Remy keeps about security documents.">
            <div className={k.bar}><img className={k.face} src={FACES.Remy} alt="" /><b>Remy</b><span>· About</span><i /><i /><i /></div>
            <div className={k.body}>
                <section>
                    <header className={v.head}>
                        <p className={v.h}>What Remy has been taught</p>
                        <p className={k.muted}>Instructions it follows when a task matches.</p>
                    </header>
                    <div className={v.deck}>
                        <div className={v.hand}>
                            <ul className={v.track}>
                                <li className={v.newCard}>
                                    <span className={v.plus}>+</span>
                                    <span className={v.newName}>New skill</span>
                                    <span className={v.newNote}>Ask Remy to write one</span>
                                </li>
                                <li className={v.card} style={{ ["--band" as string]: "var(--violet)" } as CSSProperties}>
                                    <div className={v.inner}>
                                        <Front name="Brand voice" line="How Remy works with the team." words={50} glyph={GRID} />
                                    </div>
                                </li>
                                <li className={`${v.card} ${v.fresh}`}>
                                    <div className={`${v.inner} ${v.turns}`}>
                                        <Front name="Security questionnaire" line="How we answer a buyer’s security questionnaire." words={24} glyph={STACK} />
                                        <div className={`${v.face} ${v.rear}`}>
                                            <span className={v.rearName}>Security questionnaire</span>
                                            <span className={v.acts}><span className={v.read}>Read it</span><span className={v.turn}>Turn back</span></span>
                                        </div>
                                    </div>
                                </li>
                            </ul>
                            <div className={v.oars}>
                                <span className={v.oar}><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M10 4 6 8l4 4" /></svg></span>
                                <span className={v.count}><span className={v.one}>1 skill</span><span className={v.two}>2 skills</span></span>
                                <span className={v.oar}><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M6 4l4 4-4 4" /></svg></span>
                            </div>
                        </div>

                        <div className={v.reader}>
                            <p className={v.readHead}>
                                <span><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M13 8H3M7 4 3 8l4 4" /></svg>All skills</span>
                                <code>/skills/security-questionnaire/SKILL.md</code>
                            </p>
                            <pre className={v.front}>{"---\nname: security-questionnaire\ndescription: How we answer a buyer’s security questionnaire.\n---"}</pre>
                            <p className={v.docTitle}>Security questionnaire</p>
                            <ol className={v.steps}>{STEPS.map((step) => <li key={step}>{step}</li>)}</ol>
                        </div>
                    </div>
                </section>

                <section className={v.remembers}>
                    <header className={v.head}><p className={v.h}>What Remy remembers</p></header>
                    <div className={v.note}>
                        <i className={v.noteDot} />
                        <span className={v.topic}>Security documents</span>
                        <span className={v.gloss}>· Only the approved overview goes out</span>
                        <span className={v.at}>Sat</span>
                    </div>
                </section>
            </div>

            <div className={v.composer}>
                <span className={v.typed}>
                    <span className={v.placeholder}>Ask Remy…</span>
                    <span className={v.request}>{REQUEST}</span>
                </span>
                <span className={v.send}><svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M8 13V3M4 7l4-4 4 4" /></svg></span>
            </div>

            <span className={v.pointer}><i /></span>
        </Vignette>
    );
}
