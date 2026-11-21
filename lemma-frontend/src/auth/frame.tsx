"use client";

import type { CSSProperties, ReactNode } from "react";
import { LemmaLogo } from "@/ui/icons";
import { productFace } from "@/ui/product-face";
import { characterUrl } from "@/shell/character";
import { markTint } from "@/shell/mark-tint";
import type { CharacterName } from "@/shell/cast";
import { Glyph, type GlyphName } from "./marks";

/** Who is speaking, on the left.
 *
 *  The same frame as the way in that follows a new account: whoever is asking
 *  on the left, one question on the right. So making an account runs straight
 *  on into making a first Lemma without a change of scene.
 *
 *  Each screen gets the left side that does its job. `still` wherever somebody
 *  is doing a task -- signing in, a code, a password -- so nothing pulls at the
 *  eye: Lemma's bars, still, in a soft glow. `cast` where somebody does not
 *  know yet what a Lemma is: three of them standing, with one sentence saying
 *  what one is. `preview` where there is somewhere to go: a small, true picture
 *  of the Lemma a link was opening. `other` when something else is asking: the
 *  CLI, or an app.
 *
 *  Nothing here reacts to typing. It says something only when it is true
 *  ("Signing you in", "Signed in"), and it moves once, when you are in.
 */
export type Speaker =
    | { kind: "still"; status?: string; busy?: boolean; done?: boolean }
    | { kind: "cast"; faces: CharacterName[]; caption?: string; foot?: string }
    | { kind: "preview"; id: string; name: string | null; face: CharacterName; others: CharacterName[] }
    | { kind: "other"; name: string; mark: ReactNode; status?: string; done?: boolean };

const STILL: Speaker = { kind: "still" };

/** The icon in a circle above a title that reports something: a problem, a
 *  done, mail on its way. */
export interface StateIcon {
    glyph: GlyphName;
    tone?: "warn" | "ok";
}

/** The frame, for every screen in the sign-in pages and the few outside them
 *  (Lemma Desktop's hand-off, Slack's) that borrow it.
 *
 *  `looking` is accepted and ignored: callers outside these pages still pass
 *  it, from when the characters turned away while a password was typed. */
export function Screen({ title, lead, children, footer, speaker = STILL, icon, then, back }: {
    title: string;
    lead?: ReactNode;
    children?: ReactNode;
    footer?: ReactNode;
    looking?: boolean;
    speaker?: Speaker;
    icon?: StateIcon;
    /** Where this goes next, in words: read as "Then on to {then}". */
    then?: string | null;
    /** A way back, above the title. */
    back?: ReactNode;
}) {
    return (
        <div className={productFace.variable + " auth-frame"}>
            <Side speaker={speaker} />
            <main className="auth-col">
                {/* Keyed by the title, so a new screen arrives as one -- the one
                    piece of motion on the right -- and a screen that only
                    changes its words in place does not. */}
                <div className="auth-body" key={title}>
                    {back}
                    {icon && (
                        <span className="auth-state" data-tone={icon.tone}>
                            <Glyph name={icon.glyph} size={22} />
                        </span>
                    )}
                    <h1 className="auth-title">{title}</h1>
                    {then && (
                        /* With a preview on the left, the picture already says
                           where; the words stay for a phone, which has no
                           picture. */
                        <p className="auth-then" data-phone={speaker.kind === "preview" ? "" : undefined}>
                            <Glyph name="arrow" size={14} />
                            <span>Then on to {then}</span>
                        </p>
                    )}
                    {lead && <p className="auth-lede">{lead}</p>}
                    {children}
                    {footer && <p className="auth-foot">{footer}</p>}
                </div>
            </main>
        </div>
    );
}

/** A problem, said where it happened. `alert` rather than `status`: a refusal
 *  that a screen reader announces only when it happens to be visited is one
 *  somebody retypes a correct password against, three times. */
export function Problem({ said }: { said: string | null | undefined }) {
    if (!said) return null;
    return <p className="auth-problem" role="alert"><Glyph name="warn" /><span>{said}</span></p>;
}

/** The account in play, quietly, with a way to change it: "Change" while
 *  signing in, "Not you?" once signed in. */
export function Chip({ email, action }: { email: string; action?: ReactNode }) {
    return (
        <div className="auth-chip">
            <Glyph name="mail" />
            <b>{email}</b>
            {action}
        </div>
    );
}

function Side({ speaker }: { speaker: Speaker }) {
    const status = speaker.kind === "still" || speaker.kind === "other" ? speaker.status ?? "" : "";
    const busy = speaker.kind === "still" && speaker.busy;
    const done = (speaker.kind === "still" || speaker.kind === "other") && speaker.done;
    const name = speaker.kind === "other" ? speaker.name : speaker.kind === "preview" ? speaker.name : null;
    return (
        <aside
            className="auth-side"
            data-kind={speaker.kind}
            data-busy={busy ? "" : undefined}
            data-done={done ? "" : undefined}
            aria-label={name ?? "Lemma"}
        >
            <span className="auth-side__brand"><LemmaLogo /></span>
            <div className="auth-side__center">
                {speaker.kind === "cast" && <Cast faces={speaker.faces} caption={speaker.caption} />}
                {speaker.kind === "preview" && <Peek speaker={speaker} />}
                {/* Beside the question this is the big mark; on a phone it is
                    the strip over the question, and the cast and the preview
                    give way to it. */}
                <div className="auth-speaker">
                    <span className="auth-stage" aria-hidden="true">
                        <span className="auth-glow" />
                        {speaker.kind === "other" ? <span className="auth-other">{speaker.mark}</span>
                            : speaker.kind === "preview" ? (
                                <span className="auth-face" style={{ background: markTint(speaker.id).background }}>
                                    <img src={characterUrl(speaker.face, true)} alt="" draggable={false} />
                                </span>
                            )
                            : <span className="auth-mark"><i /><i /><i /></span>}
                    </span>
                    <span className="auth-who">
                        {/* Beside the question only something other than
                            Lemma is named; in the strip, whoever it is. */}
                        {(name || speaker.kind !== "preview") && (
                            <span className="auth-who__name" data-quiet={name ? undefined : ""}>{name ?? "Lemma"}</span>
                        )}
                        <span className="auth-status" role="status" data-on={status ? "" : undefined}>
                            {status && <><i aria-hidden="true" />{status}</>}
                        </span>
                    </span>
                </div>
            </div>
            {speaker.kind === "cast" && speaker.foot && <p className="auth-side__foot">{speaker.foot}</p>}
        </aside>
    );
}

/** Three of them, standing. Pictures rather than the live rigs: they are not
 *  meant to do anything while somebody types, and three rigs is three
 *  animation loops behind a form. */
function Cast({ faces, caption }: { faces: CharacterName[]; caption?: string }) {
    return (
        <div className="auth-cast-wrap">
            <div className="auth-cast" aria-hidden="true">
                {faces.slice(0, 3).map((face, at) => (
                    <span key={face} className="auth-cast__one" style={{ background: markTint(face).background, "--at": at } as CSSProperties}>
                        <img src={characterUrl(face)} alt="" draggable={false} />
                    </span>
                ))}
            </div>
            {caption && <p className="auth-cast__caption">{caption}</p>}
        </div>
    );
}

/** A small picture of the Lemma a link was opening: its face on its tint, its
 *  name, three apps, a little text, in a window with a sidebar strip. */
function Peek({ speaker }: { speaker: Extract<Speaker, { kind: "preview" }> }) {
    const rows = [speaker.face, ...speaker.others.filter((face) => face !== speaker.face).slice(0, 2)];
    const tint = markTint(speaker.id).background;
    return (
        <div className="auth-peek">
            <div className="auth-peek__win" aria-hidden="true">
                <div className="auth-peek__side" style={{ "--peek-tint": tint } as CSSProperties}>
                    <span className="auth-peek__bars"><i /><i /><i /></span>
                    {rows.map((face, at) => (
                        <span key={face} className="auth-peek__row">
                            <b style={{ background: at === 0 ? tint : markTint(face).background }}>
                                <img src={characterUrl(face, true)} alt="" draggable={false} />
                            </b>
                            <i style={{ width: [44, 34, 40][at] }} />
                        </span>
                    ))}
                </div>
                <div className="auth-peek__main">
                    <span className="auth-peek__face" style={{ background: tint }}>
                        <img src={characterUrl(speaker.face)} alt="" draggable={false} />
                    </span>
                    {speaker.name ? <span className="auth-peek__name">{speaker.name}</span> : <span className="auth-peek__text" />}
                    <span className="auth-peek__apps">
                        {[0, 1, 2].map((at) => <i key={at} style={{ background: markTint(speaker.id + ":" + at).background }} />)}
                    </span>
                    <span className="auth-peek__text" />
                    <span className="auth-peek__text" />
                </div>
            </div>
            <p className="auth-peek__caption">Then on to <b>{speaker.name ?? "that Lemma"}</b></p>
        </div>
    );
}
