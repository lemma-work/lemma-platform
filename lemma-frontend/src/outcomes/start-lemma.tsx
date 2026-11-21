"use client";

import { useRef, useState, type FormEvent, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { source, type Org, type Pod } from "@/data";
import { ArrivalStage, type StageModel } from "@/arrival/stage";
import { GlyphIcon } from "@/arrival/glyphs";
import { characterUrl, type CharacterName } from "@/shell/character";
import { usePanelFocus } from "@/ui/focus-trap";
import { addLemmaPeople, createLemma, freshFace, type LemmaReach } from "./data";
import { useOrgPeople, type OrgPerson } from "./new-lemma";
import { SharedLink } from "./shared-link";
import { Avatar } from "./parts";

/** Ready starts: a name and what it does, filled in with one tap. */
const READY: readonly { name: string; purpose: string }[] = [
    { name: "Hiring", purpose: "Takes every candidate from the careers form to an offer" },
    { name: "Support", purpose: "Answers every customer question, and raises the hard ones" },
    { name: "Follow-ups", purpose: "Follows up every lead before it goes cold" },
    { name: "Reviews", purpose: "Reviews every pull request, so nobody waits" },
];

type Step = "name" | "people" | "apps";

/** Starting a Lemma, the way the way in does it: the whole screen, the
 *  Lemma on the left being put together while its person answers on the
 *  right. Lemma's bars listen while it is named; making it brings its face;
 *  the people added stand beside it; then the apps that fit, one tap each,
 *  going in while the reader looks around. Each question ends in a change
 *  they can see. */
export function StartLemma({ org, pods, me, onClose, onMade, apps }: {
    org: Org;
    /** The reader's name: they stand beside it once it is made. */
    me: string;
    /** The organization's Lemmas, so a new one wears a face none of them do. */
    pods: readonly Pod[];
    onClose: () => void;
    /** Made, and the reader is done here: open it. `missed` people could not
     *  be added. */
    onMade: (podId: string, missed: number) => void;
    /** The last step: apps that fit, for the Lemma just made. */
    apps?: (pod: { id: string; name: string; purpose: string }) => ReactNode;
}) {
    const cache = useQueryClient();
    const people = useOrgPeople(org.id);
    const [face] = useState<CharacterName>(() => freshFace(pods.map((one) => one.iconUrl)));
    const [step, setStep] = useState<Step>("name");
    const [name, setName] = useState("");
    const [purpose, setPurpose] = useState("");
    const [reach, setReach] = useState<LemmaReach>("invited");
    const [made, setMade] = useState<string | null>(null);
    const [picked, setPicked] = useState<readonly OrgPerson[]>([]);
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const [typing, setTyping] = useState(0);
    const [burst, setBurst] = useState(0);
    const [missed, setMissed] = useState(0);
    const box = useRef<HTMLElement>(null);
    /* Escape leaves; once it is made, leaving opens it. */
    const keys = usePanelFocus(box, () => { if (busy) return; if (made) onMade(made, missed); else onClose(); }, { focusFirst: false });

    const shown = name.trim() || "New Lemma";
    const model: StageModel = {
        pod: made ? { id: made, name: shown, iconUrl: characterUrl(face) } : null,
        slots: [],
        you: made ? me.trim().charAt(0).toUpperCase() || null : null,
        reach: [],
        mood: step === "name" ? "listening" : "idle",
        greeting: made ? 1 : 0,
        typing,
        heard: null,
        burst,
        part: step === "people" ? "people" : null,
    };

    const make = async (event: FormEvent) => {
        event.preventDefault();
        if (!name.trim() || busy) return;
        setBusy(true);
        setProblem(null);
        try {
            const done = await createLemma({ orgId: org.id, name, purpose, face, reach, people: [] });
            await cache.invalidateQueries({ queryKey: ["pods", org.id] }).catch(() => undefined);
            setMade(done.podId);
            setBurst((was) => was + 1);
            setStep(people.length > 0 ? "people" : apps ? "apps" : "people");
        } catch (failure) {
            setProblem(failure instanceof Error ? failure.message : "I couldn’t be made just now. Try again.");
        } finally {
            setBusy(false);
        }
    };

    const addPeople = async () => {
        if (!made || busy) return;
        if (picked.length > 0) {
            setBusy(true);
            setMissed(await addLemmaPeople(made, picked.map((one) => one.id)));
            setBusy(false);
        }
        if (apps) setStep("apps");
        else onMade(made, missed);
    };

    if (source.label !== "live") {
        return (
            <section className="ox-startl" role="dialog" aria-modal="true" aria-label="Start a new Lemma" ref={box} onKeyDown={keys}>
                <div className="ox-startl__col"><p className="ox-vs">Making a Lemma needs a signed-in session.</p><button type="button" className="ox-btn" onClick={onClose}>Close</button></div>
            </section>
        );
    }

    return (
        <section className="ox-startl" role="dialog" aria-modal="true" aria-label="Start a new Lemma" ref={box} onKeyDown={keys}>
            <div className="ox-startl__hero">
                <button type="button" className="ox-link ox-startl__cancel" onClick={() => (made ? onMade(made, missed) : onClose())}>{made ? "Close" : "Cancel"}</button>
                <div className="ox-startl__center">
                    <div className="ox-scene"><ArrivalStage model={model} /></div>
                    <div className="ox-lp__id">
                        <p className="ox-lp__name">{shown}</p>
                        <span className="ox-lp__status" data-tone={made ? "ok" : "live"}><i />{made ? (step === "people" ? "Just made" : "Ready") : "Listening"}</span>
                    </div>
                    {picked.length > 0 && (
                        <div className="ox-startl__people" aria-label="People in it">
                            {picked.map((one) => <Avatar key={one.id} name={one.label} size={26} />)}
                        </div>
                    )}
                </div>
            </div>
            <div className="ox-startl__col">
                {step === "name" && (
                    <form key="name" className="ox-startl__q" onSubmit={(event) => void make(event)}>
                        <h1 className="ox-startl__t">What’s this Lemma for?</h1>
                        <label className="ox-startl__big">
                            <input value={name} onChange={(event) => { setName(event.target.value); setTyping((was) => was + 1); }} placeholder="Give it a name" autoFocus required maxLength={80} autoComplete="off" />
                            <span>Its name. You can change it later.</span>
                        </label>
                        <label className="ox-startl__big ox-startl__big--line">
                            <input value={purpose} onChange={(event) => { setPurpose(event.target.value); setTyping((was) => was + 1); }} placeholder="What it does, in a sentence" maxLength={240} autoComplete="off" />
                            <span>It works from this, and learns the rest by doing.</span>
                        </label>
                        <p className="ox-lp__label">Or start from one that’s ready</p>
                        <div className="ox-lp__chips">
                            {READY.map((one) => (
                                <button key={one.name} type="button" className="ox-lp__chip" aria-pressed={name === one.name && purpose === one.purpose} onClick={() => { setName(one.name); setPurpose(one.purpose); setTyping((was) => was + 1); }}>{one.name}</button>
                            ))}
                        </div>
                        <div className="ox-startl__reach" role="radiogroup" aria-label="Who can find it">
                            <button type="button" role="radio" aria-checked={reach === "invited"} onClick={() => setReach("invited")}>Only people I add</button>
                            <button type="button" role="radio" aria-checked={reach === "org"} onClick={() => setReach("org")}>Everyone at {org.name}</button>
                        </div>
                        {problem && <p className="ox-note" role="alert">{problem}</p>}
                        <div className="ox-startl__acts">
                            <span className="ox-startl__hint">{reach === "invited" ? "Only the people you add will see it." : `Anyone at ${org.name} can find it and join.`}</span>
                            <button type="submit" className="ox-btn ox-btn--primary ox-startl__go" disabled={busy || !name.trim()}>{busy ? "Making…" : "Make it"}</button>
                        </div>
                        <details className="ox-startl__shared">
                            <summary>Or bring in one someone shared</summary>
                            <SharedLink />
                        </details>
                    </form>
                )}
                {step === "people" && made && (
                    <div key="people" className="ox-startl__q">
                        <h1 className="ox-startl__t">Hi, I’m {shown}. Who should work with me?</h1>
                        <p className="ox-startl__lede">Whoever you add sees what’s here and can ask me anything.</p>
                        {people.length > 0 ? (
                            <div className="ox-lp__chips">
                                {people.slice(0, 12).map((one) => {
                                    const on = picked.some((was) => was.id === one.id);
                                    return (
                                        <button key={one.id} type="button" className="ox-lp__chip ox-startl__person" aria-pressed={on} onClick={() => setPicked((was) => (on ? was.filter((x) => x.id !== one.id) : [...was, one]))}>
                                            <Avatar name={one.label} size={20} />{one.label}
                                        </button>
                                    );
                                })}
                            </div>
                        ) : <p className="ox-vs">Nobody else is at {org.name} yet. Invite people from its page once you’re in.</p>}
                        <div className="ox-startl__acts">
                            <span className="ox-startl__hint">{picked.length === 0 ? "Just you for now is fine too." : `${picked.length} ${picked.length === 1 ? "person" : "people"} with you.`}</span>
                            <button type="button" className="ox-btn ox-btn--primary ox-startl__go" disabled={busy} onClick={() => void addPeople()}>{busy ? "Adding…" : "Continue"}</button>
                        </div>
                    </div>
                )}
                {step === "apps" && made && apps && (
                    <div key="apps" className="ox-startl__q">
                        <h1 className="ox-startl__t">These fit {shown}. Add the ones you want.</h1>
                        {apps({ id: made, name: shown, purpose })}
                        <div className="ox-startl__acts">
                            <span className="ox-startl__hint">They go in while you look around.</span>
                            <button type="button" className="ox-btn ox-btn--primary ox-startl__go" onClick={() => onMade(made, missed)}>Open {shown}</button>
                        </div>
                    </div>
                )}
            </div>
        </section>
    );
}

/** An app's mark in its circle, for a row of apps that fit. */
export function AppMark({ slug }: { slug: string }) {
    return <span className="ox-startl__glyph"><GlyphIcon slug={slug} size={18} /></span>;
}
