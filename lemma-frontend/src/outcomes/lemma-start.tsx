"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { Pod } from "@/data";
import { characterUrl, CHARACTERS, type CharacterName } from "@/shell/character";
import { TeammateFace } from "@/space/teammate-face";
import type { FrameActions } from "./actions";
import { AskBox } from "./ask";
import { addTemplate, useShelf } from "./data";
import { useNewPage } from "./lemma-tabs";
import { bringInRequest, outcomeView, templateSetupRequest, type Ask, type Go, type ShelfCard } from "./model";
import { Icon, to, type IconName } from "./parts";

/** Where a team's stuff usually is, and the tab each one lands in. The
 *  assistant does the moving (the lemma-bring-in skill); this only names the
 *  places, so a person picks rather than types. */
export const BRING_FROM: readonly { name: string; into: string; mark: string }[] = [
    { name: "Notion", into: "Into Pages", mark: "N" },
    { name: "Google Drive", into: "Into Files", mark: "D" },
    { name: "Airtable", into: "Into Tables", mark: "A" },
    { name: "Google Sheets", into: "Into Tables", mark: "S" },
    { name: "Slack", into: "Into Chats: it answers in Slack too", mark: "#" },
];

/** How many templates the start shows before "All". */
const TEMPLATES_SHOWN = 3;

/** An empty Lemma's first page. The organization's own Lemma opens on its
 *  welcome: where the team's stuff is today, brought in. Any other opens on
 *  four ways in -- ask it, bring things in, start from a template, or just
 *  make something -- each one click from doing something. */
export function LemmaStart({ pod, welcome, go, onAsk, actions }: {
    pod: Pod;
    /** The organization's own Lemma, made with it: its start leads with
     *  bringing the team's stuff in. */
    welcome: boolean;
    go: Go;
    onAsk: Ask;
    actions: FrameActions;
}) {
    const [plain, setPlain] = useState(false);
    if (welcome && !plain) return <Welcome pod={pod} onAsk={onAsk} onPlain={() => setPlain(true)} />;
    return (
        <div className="ox-page ox-start">
            <h1 className="ox-start__t">{pod.name} is empty. Start it any way you like.</h1>
            <div className="ox-ways">
                <section className="ox-way" aria-labelledby="way-ask">
                    <h2 id="way-ask"><Icon name="chat" size={16} />Ask {pod.name}</h2>
                    <p className="ox-vs">Say what it’s for. It builds the tables, pages and automations.</p>
                    <AskBox pods={[pod]} podId={pod.id} onAsk={onAsk} placeholder={pod.description ? `${pod.description}…` : "Track our hiring, from the careers form to an offer"} />
                </section>
                <section className="ox-way" aria-labelledby="way-bring">
                    <h2 id="way-bring"><Icon name="download" size={16} />Bring it in</h2>
                    <p className="ox-vs">From the tools this work is in today.</p>
                    <div className="ox-way__chips">
                        {BRING_FROM.map((place) => (
                            <button key={place.name} type="button" className="ox-way__chip" onClick={() => actions.prompt(pod.id, bringInRequest([place.name]))}>
                                <span className="ox-mono" aria-hidden="true">{place.mark}</span>{place.name}
                            </button>
                        ))}
                        <button type="button" className="ox-way__chip" onClick={() => actions.prompt(pod.id, "Bring in ")}>
                            <span className="ox-mono" aria-hidden="true">+</span>Something else
                        </button>
                    </div>
                </section>
                <Templates pod={pod} onAsk={onAsk} />
                <Make pod={pod} go={go} actions={actions} />
            </div>
        </div>
    );
}

/** The organization's own Lemma, on the first day: what it holds, in one
 *  sentence, then where the team's stuff is today. Picking places and
 *  "Bring in" sends the assistant one message naming them. */
function Welcome({ pod, onAsk, onPlain }: { pod: Pod; onAsk: Ask; onPlain: () => void }) {
    const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
    const flip = (name: string) => setPicked((was) => {
        const next = new Set(was);
        if (next.has(name)) next.delete(name);
        else next.add(name);
        return next;
    });
    const names = BRING_FROM.map((place) => place.name).filter((name) => picked.has(name));
    return (
        <div className="ox-page ox-welcome">
            <p className="ox-vs">{pod.name} holds your team’s chats, pages, tables, files and apps, with an assistant that works in all of them.</p>
            <h1 className="ox-welcome__t">Where’s your team’s stuff today?</h1>
            <p className="ox-vs">{pod.name} brings it in. Pick as many as you like.</p>
            <div className="ox-places" role="group" aria-label="Where your team’s stuff is">
                {BRING_FROM.map((place) => (
                    <button key={place.name} type="button" className="ox-place-tile" aria-pressed={picked.has(place.name)} onClick={() => flip(place.name)}>
                        <span className="ox-mono" aria-hidden="true">{place.mark}</span>
                        <span className="ox-place-tile__t"><span>{place.name}</span><span className="ox-vs">{place.into}</span></span>
                        <span className="ox-place-tile__tick" aria-hidden="true"><Icon name="check" size={12} /></span>
                    </button>
                ))}
            </div>
            <div className="ox-welcome__acts">
                <button type="button" className="ox-btn ox-btn--primary" disabled={names.length === 0} onClick={() => onAsk(pod.id, bringInRequest(names))}>
                    {names.length ? `Bring in ${names.length}` : "Bring in"}
                </button>
                <button type="button" className="ox-btn" onClick={onPlain}>Start another way</button>
            </div>
        </div>
    );
}

/** Templates as kits to add to this Lemma: tables, skills and automations
 *  that land in it, after which it sets them up in a chat. */
function Templates({ pod, onAsk }: { pod: Pod; onAsk: Ask }) {
    const shelf = useShelf();
    const queryClient = useQueryClient();
    const [all, setAll] = useState(false);
    const [adding, setAdding] = useState<{ id: string; said: string } | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const cards = shelf.data ?? [];
    const shown = all ? cards : cards.slice(0, TEMPLATES_SHOWN);

    const add = async (card: ShelfCard) => {
        setAdding({ id: card.id, said: "Adding…" });
        setProblem(null);
        try {
            await addTemplate(pod.id, card.id, (said) => setAdding({ id: card.id, said }));
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: ["outcome-tables", pod.id] }),
                queryClient.invalidateQueries({ queryKey: ["library", pod.id] }),
            ]).catch(() => undefined);
            onAsk(pod.id, templateSetupRequest(card.name));
        } catch (failure) {
            setProblem(failure instanceof Error ? failure.message : "That template couldn’t be added.");
        } finally {
            setAdding(null);
        }
    };

    return (
        <section className="ox-way" aria-labelledby="way-template">
            <h2 id="way-template"><Icon name="grid" size={16} />Start from a template</h2>
            {shelf.isPending ? <p className="ox-vs">Reading the templates…</p>
                : shelf.isError ? <p className="ox-vs">Couldn’t read the templates. <button type="button" className="ox-textbtn" onClick={() => void shelf.refetch()}>Try again</button></p>
                : (
                    <ul className="ox-kits">
                        {shown.map((card) => (
                            <li key={card.id}>
                                <KitFace face={card.face} name={card.name} />
                                <span className="ox-kits__t"><span>{card.name}</span><span className="ox-vs">{card.brings.join(" · ") || card.work}</span></span>
                                <button type="button" className="ox-btn ox-btn--small" disabled={Boolean(adding)} onClick={() => void add(card)}>
                                    {adding?.id === card.id ? adding.said : "Add"}
                                </button>
                            </li>
                        ))}
                    </ul>
                )}
            {problem && <p className="ox-note" role="alert">{problem}</p>}
            {cards.length > TEMPLATES_SHOWN && (
                <button type="button" className="ox-textbtn" onClick={() => setAll((was) => !was)}>{all ? "Fewer" : `All ${cards.length} templates`}</button>
            )}
        </section>
    );
}

function KitFace({ face, name }: { face: string | null; name: string }) {
    const known = CHARACTERS.find((one) => one === face) as CharacterName | undefined;
    if (!known) return <span className="ox-mono" aria-hidden="true"><Icon name="grid" size={13} /></span>;
    return <TeammateFace pod={{ id: name, name, iconUrl: characterUrl(known) }} size={24} />;
}

/** Making something by hand, as in any other tool. Tables and apps are made
 *  by the Lemma from a sentence, so those start the sentence. */
function Make({ pod, go, actions }: { pod: Pod; go: Go; actions: FrameActions }) {
    const { make, making, failed } = useNewPage(pod, go);
    const things: readonly { label: string; icon: IconName; run: () => void }[] = [
        { label: making ? "Making…" : "New page", icon: "page", run: () => void make("page", "Untitled", "# Untitled\n\n") },
        { label: "New table", icon: "table", run: () => actions.prompt(pod.id, "Set up a table to track ") },
        { label: "New app", icon: "app", run: () => actions.prompt(pod.id, "Build an app that ") },
    ];
    return (
        <section className="ox-way" aria-labelledby="way-make">
            <h2 id="way-make"><Icon name="plus" size={16} />Make something</h2>
            <p className="ox-vs">A page, a table, an app or a file. {pod.name} can help with any of it.</p>
            <div className="ox-way__chips">
                {things.map((thing) => (
                    <button key={thing.label} type="button" className="ox-way__chip" disabled={Boolean(making)} onClick={thing.run}>
                        <Icon name={thing.icon} size={15} />{thing.label}
                    </button>
                ))}
                <a className="ox-way__chip" {...to(outcomeView(pod.id, "files"), go)}><Icon name="upload" size={15} />Upload files</a>
            </div>
            {failed && <p className="ox-note" role="alert">That page couldn’t be made. Try again.</p>}
        </section>
    );
}
