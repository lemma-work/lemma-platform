"use client";

import { useMemo, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { source, type Org } from "@/data";
import { characterUrl, CHARACTERS, type CharacterName } from "@/shell/character";
import { kindOf, startLemma, useShelf } from "./data";
import { chatView, ownName, sentenceFor, sharedLinkPath, shelfOf, type Go, type ShelfCard, type ShelfWork } from "./model";
import { Empty, Icon } from "./parts";
import { TeammateFace } from "@/space/teammate-face";

/** A template's face, drawn the way a Lemma's is, so the one you pick is the
 *  one you get: starting it wears the same face. */
function Face({ face, name, size }: { face: string | null; name: string; size: number }) {
    const known = CHARACTERS.find((one) => one === face) as CharacterName | undefined;
    if (!known) return <span className="ox-shelf__face" style={{ width: size, height: size }} aria-hidden="true"><Icon name="plus" size={16} /></span>;
    return <TeammateFace pod={{ id: name, name, iconUrl: characterUrl(known) }} size={size} />;
}

/** Where a new Lemma starts: the kind of work, then what it owns there, from
 *  the templates the server ships -- or the owner's own words, or something
 *  someone shared. Starting one lands in the chat it sets itself up in. */
export function Shelf({ org, go }: { org: Org; go: Go }) {
    const shelf = useShelf();
    const works = useMemo(() => shelfOf(shelf.data ?? []), [shelf.data]);
    const [picked, setPicked] = useState<string | "own" | null>(null);
    const work = works.find((one) => one.id === picked) ?? null;

    if (source.label !== "live") {
        return <div className="ox-page"><Empty title="Starting a Lemma needs a signed-in session."><p>The templates come from the server.</p></Empty></div>;
    }
    if (picked === "own") return <OwnWords org={org} go={go} onBack={() => setPicked(null)} />;
    if (work) return <WorkPage org={org} work={work} go={go} onBack={() => setPicked(null)} />;

    return (
        <div className="ox-page ox-shelf">
            <h1 className="ox-title">What do you do?</h1>
            <p className="ox-sub">Pick your work. Each comes with what a Lemma can own there, and works with the tools you already use.</p>
            {shelf.isPending ? <p className="ox-loading">Reading what a Lemma can start from…</p> : shelf.isError ? (
                <Empty title="Couldn’t read what a Lemma can start from.">
                    <button type="button" className="ox-btn" onClick={() => void shelf.refetch()}>Try again</button>
                </Empty>
            ) : (
                <div className="ox-shelf__works">
                    {works.map((one) => (
                        <button key={one.id} type="button" className="ox-shelf__work" onClick={() => setPicked(one.id)}>
                            <Face face={one.face} name={one.label} size={40} />
                            <span className="ox-shelf__t">
                                <span className="ox-shelf__name">{one.label}</span>
                                <span className="ox-shelf__line">{one.cards.map((card) => card.name).join(" · ")}</span>
                                {one.places.length > 0 && <span className="ox-vs">Usually in {one.places.join(", ")}</span>}
                            </span>
                            <span className="ox-vs ox-shelf__n">{one.cards.length} to own</span>
                        </button>
                    ))}
                    <button type="button" className="ox-shelf__work ox-shelf__work--own" onClick={() => setPicked("own")}>
                        <Face face={null} name="Something else" size={40} />
                        <span className="ox-shelf__t">
                            <span className="ox-shelf__name">Something else</span>
                            <span className="ox-shelf__line">Say what it should own, in your words.</span>
                        </span>
                    </button>
                </div>
            )}
            <SharedLink />
        </div>
    );
}

/** A link to a Lemma or a plugin someone shared, opened where this app
 *  installs it: what it brings and needs is shown before anything is added. */
function SharedLink() {
    const [link, setLink] = useState("");
    const [wrong, setWrong] = useState(false);
    const open = (event: FormEvent) => {
        event.preventDefault();
        const path = sharedLinkPath(link);
        setWrong(path === null);
        if (path) window.location.assign(path);
    };
    return (
        <section className="ox-shelf__shared">
            <div className="ox-sec"><h2>Bring one someone shared</h2></div>
            <form className="ox-own" onSubmit={open}>
                <label htmlFor="shared-link">Link</label>
                <input id="shared-link" value={link} onChange={(event) => { setLink(event.target.value); setWrong(false); }} placeholder="A GitHub repository, or a link someone shared from Lemma" />
                <button type="submit" className="ox-btn" disabled={!link.trim()}>Look</button>
            </form>
            <p className="ox-vs" role={wrong ? "alert" : undefined}>{wrong ? "That isn’t a link this can open. Paste a GitHub repository or a Lemma share link." : "You see what it brings and what it needs before anything is added."}</p>
        </section>
    );
}

/** The Lemmas one kind of work can start, and what each brings. */
function WorkPage({ org, work, go, onBack }: { org: Org; work: ShelfWork; go: Go; onBack: () => void }) {
    const queryClient = useQueryClient();
    const [chosen, setChosen] = useState<ShelfCard>(work.cards[0]);
    const [due, setDue] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const needsDate = chosen.kind === "date";

    const start = async () => {
        setBusy(true);
        setError(null);
        try {
            const { podId, conversationId } = await startLemma({ organization_id: org.id, template: chosen.id, due: needsDate ? due : null }, chosen.face);
            await queryClient.invalidateQueries({ queryKey: ["pods", org.id] });
            go(chatView({ kind: "chat", podId, id: conversationId }));
        } catch (failure) {
            setError(failure instanceof Error ? failure.message : "Couldn’t start it");
            setBusy(false);
        }
    };

    return (
        <div className="ox-page ox-shelf">
            <button type="button" className="ox-textbtn" onClick={onBack}><Icon name="back" size={14} /> All work</button>
            <div className="ox-shelf__cols">
                <div className="ox-shelf__main">
                    <header className="ox-shelf__head">
                        <Face face={chosen.face} name={chosen.name} size={48} />
                        <h1 className="ox-title">{work.label}</h1>
                    </header>

                    <div className="ox-sec"><h2>What it can own</h2></div>
                    <div className="ox-shelf__opts" role="radiogroup" aria-label="What it can own">
                        {work.cards.map((card) => (
                            <button key={card.id} type="button" role="radio" aria-checked={card.id === chosen.id} className="ox-shelf__opt" onClick={() => { setChosen(card); setError(null); }}>
                                <span className="ox-shelf__radio" aria-hidden="true" />
                                <span className="ox-shelf__t">
                                    <span>{sentenceFor(card, card.id === chosen.id ? due : null)}</span>
                                    <span className="ox-vs">{card.name} · {card.kind === "date" ? "A finish line, on a date you pick" : "A bar it holds every week"}</span>
                                </span>
                            </button>
                        ))}
                    </div>

                    {chosen.place && (
                        <>
                            <div className="ox-sec"><h2>Where it works</h2><span className="ox-vs">It asks which one once it starts</span></div>
                            <p className="ox-shelf__said">Usually {chosen.place}, or wherever this work already happens for you. It reads what is there, and keeps reading.</p>
                        </>
                    )}

                    {chosen.brings.length > 0 && (
                        <>
                            <div className="ox-sec"><h2>What it brings</h2></div>
                            <ul className="ox-shelf__list">{chosen.brings.map((line) => <li key={line}>{line}</li>)}</ul>
                        </>
                    )}

                    {chosen.never.length > 0 && (
                        <>
                            <div className="ox-sec"><h2>Never</h2></div>
                            <ul className="ox-shelf__list">{chosen.never.map((line) => <li key={line}>{line}</li>)}</ul>
                        </>
                    )}
                </div>

                <aside className="ox-shelf__start" aria-label={"Start " + chosen.name}>
                    <p className="ox-shelf__name">{chosen.name}</p>
                    <p className="ox-vs">Will own</p>
                    <p>{sentenceFor(chosen, due || null)}</p>
                    {needsDate && <label className="ox-shelf__due">By<input type="date" value={due} onChange={(event) => setDue(event.target.value)} /></label>}
                    <button type="button" className="ox-btn ox-btn--primary ox-shelf__go" disabled={busy || (needsDate && !due)} onClick={() => void start()}>{busy ? "Starting…" : "Start " + chosen.name}</button>
                    <p className="ox-vs">Makes a Lemma called {chosen.name} in {org.name}. It sets itself up in a chat with you.</p>
                    {error && <p className="ox-note" role="alert">{error}</p>}
                </aside>
            </div>
        </div>
    );
}

/** A Lemma from its owner's own words: what it owns, and a name. It starts
 *  without a number and sets how it is counted once it has built its tables. */
function OwnWords({ org, go, onBack }: { org: Org; go: Go; onBack: () => void }) {
    const queryClient = useQueryClient();
    const [sentence, setSentence] = useState("");
    const [kind, setKind] = useState<"week" | "date">("week");
    const [due, setDue] = useState("");
    const [name, setName] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const called = name ?? (sentence.trim() ? ownName(sentence) : "");
    const ready = sentence.trim() && called.trim() && (kind === "week" || due);

    const start = async (event: FormEvent) => {
        event.preventDefault();
        if (!ready) return;
        setBusy(true);
        setError(null);
        try {
            const { podId, conversationId } = await startLemma({
                organization_id: org.id,
                name: called.trim(),
                outcome: { sentence: sentence.trim(), kind: kindOf(kind), due: kind === "date" ? due : null },
            }, null);
            await queryClient.invalidateQueries({ queryKey: ["pods", org.id] });
            go(chatView({ kind: "chat", podId, id: conversationId }));
        } catch (failure) {
            setError(failure instanceof Error ? failure.message : "Couldn’t start it");
            setBusy(false);
        }
    };

    return (
        <div className="ox-page ox-shelf">
            <button type="button" className="ox-textbtn" onClick={onBack}><Icon name="back" size={14} /> All work</button>
            <h1 className="ox-title">What should it own?</h1>
            <p className="ox-sub">One sentence a person can check. It builds the tables that count it, then sets how it is counted.</p>
            <form className="ox-shelf__form" onSubmit={(event) => void start(event)}>
                <label>It owns<input value={sentence} onChange={(event) => setSentence(event.target.value)} placeholder="Every invoice is paid within 30 days" autoFocus /></label>
                <div className="ox-chips" role="group" aria-label="Kind">
                    <button type="button" className="ox-chip" aria-pressed={kind === "week"} onClick={() => setKind("week")}>A bar every week</button>
                    <button type="button" className="ox-chip" aria-pressed={kind === "date"} onClick={() => setKind("date")}>A finish line by a date</button>
                </div>
                {kind === "date" && <label>By<input type="date" value={due} onChange={(event) => setDue(event.target.value)} /></label>}
                <label>Called<input value={called} onChange={(event) => setName(event.target.value)} placeholder="Invoices" /></label>
                <div className="ox-plan__acts">
                    <button type="submit" className="ox-btn ox-btn--primary" disabled={busy || !ready}>{busy ? "Starting…" : "Start it"}</button>
                </div>
                {error && <p className="ox-note" role="alert">{error}</p>}
            </form>
        </div>
    );
}
