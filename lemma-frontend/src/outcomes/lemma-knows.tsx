"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { listStamp } from "@/data/stamp";
import { isForbidden, isMissing } from "@/session/auth-state";
import { lemma } from "@/session/client";
import { splitDescription } from "@/skills/skill-frontmatter";
import { readSkills, skillsKey, SKILLS_FRESH } from "@/skills/read-skills";
import { sayUnloadable, type SkillCard } from "@/skills/skills";
import { revisedOn } from "@/skills/skills-view";
import { useMemoryNotes } from "@/space/what-it-remembers";
import type { MemoryNote } from "@/thread/memory-notes";
import type { FrameActions } from "./actions";
import type { Go } from "./model";
import { Icon } from "./parts";
import { Calm, Problem } from "./calm";
import { TryAgain, codeOf } from "./read-again";
import { SkeletonRows } from "@/ui/skeleton";

type PageProps = { pod: Pod; go: Go; actions: FrameActions };

/* ── asking it to learn ──────────────────────────────────────────────── */

/** Something a person asked a Lemma to keep, before it is kept.
 *
 *  The page never writes the note or the skill itself: the words go to the
 *  Lemma in a chat, and it writes the file there, where the person can see
 *  what it kept. So the page holds the ask until a file shows up that was
 *  not there when it was made. `seen` is what was there then, so the test
 *  does not lean on the browser's clock agreeing with the server's. */
interface Asked {
    id: number;
    text: string;
    at: number;
    seen: readonly string[] | null;
}

/** Asks outlive the page: asking opens the chat, so the page that asked is
 *  gone by the time the answer could be written. Held per Lemma and page for
 *  as long as an answer is still likely. */
const ASKED = new Map<string, readonly Asked[]>();
const ASKED_FOR = 30 * 60_000;
/** How long "writing it down" stays the honest description of an ask. */
const WRITING_FOR = 3 * 60_000;
/** Slack for an ask made before the list it is compared with was read. */
const SKEW = 15_000;
/** How long an answer to an ask is shown as having just landed. */
const LANDED_FOR = 5 * 60_000;
let askedCount = 0;

function useAsked(place: string) {
    const [list, setList] = useState<readonly Asked[]>(() => (ASKED.get(place) ?? []).filter((one) => Date.now() - one.at < ASKED_FOR));
    const write = (next: readonly Asked[]) => {
        ASKED.set(place, next);
        setList(next);
    };
    return {
        list,
        add: (text: string, seen: readonly string[] | null) => {
            askedCount += 1;
            write([{ id: askedCount, text, at: Date.now(), seen }, ...list]);
        },
        dismiss: (id: number) => write(list.filter((one) => one.id !== id)),
    };
}

/** One thing on a page an ask could be answered by: a note or a skill, as
 *  the file it is and when that file changed. */
type Present = { key: string; at: number };

/** Which asks are still waiting, and which things on the page answered the
 *  rest: anything not in an ask's `seen`, or changed after it when nothing
 *  had been read yet. Derived on every render rather than stored, so a
 *  note that lands while the page is away is matched when it comes back.
 *  An answer counts as just landed only while it is recent. */
function settle(asked: readonly Asked[], present: readonly Present[] | undefined, now = Date.now()) {
    if (!present) return { waiting: asked, landed: new Set<string>() };
    const landed = new Set<string>();
    const waiting = asked.filter((one) => {
        const answers = present.filter((thing) => (one.seen ? !one.seen.includes(thing.key) : thing.at >= one.at - SKEW));
        answers.forEach((thing) => {
            if (now - thing.at < LANDED_FOR) landed.add(thing.key);
        });
        return answers.length === 0;
    });
    return { waiting, landed };
}

/** The line under an ask: what is happening, while that is still the
 *  likely truth, and after that only what is known -- when it was asked. */
function askedLine(one: Asked, writing: string, now: number): string {
    if (source.label !== "live") return "Waiting in our chat for you to send it";
    if (now - one.at < WRITING_FOR) return writing;
    return "Asked in our chat at " + new Date(one.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** The box that asks a Lemma to learn something. A form rather than a
 *  dialog, because what happens next is a conversation: the words open a
 *  chat, and the Lemma asks what it needs there before it writes anything. */
function TeachBox({ label, placeholder, button, hint, onSend }: {
    label: string;
    placeholder: string;
    button: string;
    hint: string;
    onSend: (text: string) => void;
}) {
    const [text, setText] = useState("");
    const ready = text.trim().length > 0;
    return (
        <div className="ox-knows-teach">
            <form
                className="ox-knows-teach__box"
                onSubmit={(event) => {
                    event.preventDefault();
                    const said = text.trim();
                    if (!said) return;
                    setText("");
                    onSend(said);
                }}
            >
                <input aria-label={label} placeholder={placeholder} value={text} autoComplete="off" onChange={(event) => setText(event.target.value)} />
                <button type="submit" className="ox-btn ox-btn--primary" disabled={!ready}>{button}</button>
            </form>
            <p className="ox-knows-teach__hint">{hint}</p>
        </div>
    );
}

/* ── memory ──────────────────────────────────────────────────────────── */

const noteKey = (note: MemoryNote) => note.path + "@" + note.updated;
const presentNotes = (notes: readonly MemoryNote[] | undefined): Present[] | undefined =>
    notes?.map((note) => ({ key: noteKey(note), at: Date.parse(note.updated) || 0 }));

/** A Lemma's Memory page: what it has written down while working, read from
 *  the note files themselves, so every row is a file that exists and nothing
 *  is a summary the model was asked to give of itself.
 *
 *  A note's file name is its topic, and the same topic can be kept twice --
 *  once for everyone, once for one person -- so notes are grouped by topic,
 *  each saying where it is kept and when it changed. Forgetting deletes the
 *  file, which cannot be undone, so it asks in the row first. Telling it
 *  something new goes through a chat, where it does the writing. */
export function MemoryPage({ pod, go, actions }: PageProps) {
    const cache = useQueryClient();
    const asked = useAsked("memory:" + pod.id);
    /* Read again while something told to it is still being written down,
       and not once it is there. */
    const notes = useMemoryNotes(pod, { poll: (data) => (settle(asked.list, presentNotes(data)).waiting.length > 0 ? 10_000 : false) });
    const [confirming, setConfirming] = useState<string | null>(null);
    const [going, setGoing] = useState<Readonly<Record<string, "going" | "gone">>>({});
    const [failed, setFailed] = useState<Readonly<Record<string, { text: string; again: boolean }>>>({});
    const [said, setSaid] = useState("");

    const { waiting, landed } = settle(asked.list, presentNotes(notes.data));
    /* One topic can be kept in more than one place; the notes arrive newest
       first, so the topics do too. */
    const topics = useMemo(() => {
        const byTopic = new Map<string, MemoryNote[]>();
        for (const note of notes.data ?? []) {
            const key = note.topic.toLowerCase();
            byTopic.set(key, [...(byTopic.get(key) ?? []), note]);
        }
        return [...byTopic.values()];
    }, [notes.data]);

    function tell(text: string) {
        asked.add(text, notes.data ? notes.data.map(noteKey) : null);
        actions.ask(pod.id, "Remember this: " + text);
    }

    async function forget(note: MemoryNote) {
        setConfirming(null);
        setGoing((was) => ({ ...was, [note.path]: "going" }));
        setFailed(({ [note.path]: _dropped, ...rest }) => rest);
        const sample = source.label !== "live";
        try {
            /* Already gone is what was asked for. */
            if (!sample) await lemma(pod.id).files.delete(note.path).catch((problem: unknown) => { if (!isMissing(problem)) throw problem; });
            setGoing((was) => ({ ...was, [note.path]: "gone" }));
            setSaid("Forgotten: " + note.topic + ".");
            /* Long enough for the row to fade out, then the list without it. */
            window.setTimeout(() => {
                cache.setQueryData<MemoryNote[]>(["memory-notes", pod.id], (was) => was?.filter((one) => one.path !== note.path));
                /* A note written again at this path later is a new note. */
                setGoing(({ [note.path]: _dropped, ...rest }) => rest);
                if (!sample) {
                    void cache.invalidateQueries({ queryKey: ["memory-notes", pod.id] });
                    void cache.invalidateQueries({ queryKey: ["library", pod.id, "files"] });
                }
            }, 260);
        } catch (problem) {
            setGoing(({ [note.path]: _dropped, ...rest }) => rest);
            setFailed((was) => ({
                ...was,
                [note.path]: isForbidden(problem)
                    ? { text: "You can’t remove this one. Whoever looks after " + pod.name + " can.", again: false }
                    : { text: "Couldn’t forget that.", again: true },
            }));
        }
    }

    const now = Date.now();
    let body: ReactNode;
    if (notes.isPending) {
        body = <SkeletonRows label={"Reading " + pod.name + "’s memory"} rows={4} slow={"Still reading " + pod.name + "’s memory. It’s slower than usual."} />;
    } else if (notes.isError && isForbidden(notes.error)) {
        body = <Calm icon="lock" title={"You can’t read " + pod.name + "’s notes"}>Whoever looks after {pod.name} can.</Calm>;
    } else if (notes.isError) {
        body = (
            <Calm tone="warn" title={"Couldn’t read " + pod.name + "’s memory"} note={codeOf(notes.error)} actions={<TryAgain busy={notes.isFetching} onClick={() => void notes.refetch()} />}>
                Nothing in it changed. You can still tell {pod.name} something above.
            </Calm>
        );
    } else if (topics.length === 0 && waiting.length === 0) {
        body = <p className="ox-knows-quiet">I haven’t written anything down yet. Tell me something above, or correct me once and I will.</p>;
    } else {
        body = (
            <div className="ox-knows-mem">
                {waiting.length > 0 && (
                    <section className="ox-knows-topic" aria-label="Things you just told me">
                        <h3>You told me</h3>
                        <ul>
                            {waiting.map((one) => (
                                <li key={one.id} className="ox-knows-note" data-writing="">
                                    <div className="ox-knows-note__body">
                                        <p className="ox-knows-note__t">“{one.text}”</p>
                                        <span className="ox-knows-note__src">
                                            {source.label === "live" && now - one.at < WRITING_FOR && <i className="ox-knows-spin" aria-hidden="true" />}
                                            {askedLine(one, "Writing it down in our chat…", now)}
                                        </span>
                                    </div>
                                    <span className="ox-knows-note__acts">
                                        <button type="button" className="ox-link" onClick={() => asked.dismiss(one.id)}>Dismiss</button>
                                    </span>
                                </li>
                            ))}
                        </ul>
                    </section>
                )}
                {topics.map((kept) => (
                    <section key={kept[0].topic} className="ox-knows-topic" aria-label={kept[0].topic}>
                        <h3>
                            {kept[0].topic}
                            {kept.length > 1 && <span className="ox-knows-num">{kept.length}</span>}
                        </h3>
                        <ul>
                            {kept.map((note) => {
                                const state = going[note.path];
                                const failure = failed[note.path];
                                const changed = listStamp(note.updated);
                                return (
                                    <li key={note.path} className="ox-knows-note" data-fresh={landed.has(noteKey(note)) || undefined} data-gone={state === "gone" || undefined}>
                                        <div className="ox-knows-note__body">
                                            {note.gloss && <p className="ox-knows-note__t">{note.gloss}</p>}
                                            <span className="ox-knows-note__src">
                                                {note.private ? "Only you" : "Shared"}
                                                {changed && " · changed " + changed}
                                            </span>
                                            {confirming === note.path && (
                                                <div className="ox-knows-confirm" role="alertdialog" aria-label={"Forget " + note.topic}>
                                                    <span>Forget this? I can’t get it back.</span>
                                                    <span className="ox-knows-confirm__acts">
                                                        <button type="button" className="ox-btn ox-btn--small ox-knows-bad" onClick={() => void forget(note)}>Forget</button>
                                                        <button type="button" className="ox-btn ox-btn--small" onClick={() => setConfirming(null)}>Keep it</button>
                                                    </span>
                                                </div>
                                            )}
                                            {failure && (
                                                <div className="ox-knows-note__bad">
                                                    <Problem onRetry={failure.again ? () => void forget(note) : undefined}>{failure.text}</Problem>
                                                </div>
                                            )}
                                        </div>
                                        {state ? (
                                            <span className="ox-knows-note__acts" data-on="" role="status">{state === "going" ? "Forgetting…" : "Forgotten"}</span>
                                        ) : confirming !== note.path && (
                                            <span className="ox-knows-note__acts">
                                                <button type="button" className="ox-link" aria-label={"Open " + note.topic} onClick={() => go({ name: "file", podId: pod.id, path: note.path })}>Open</button>
                                                <button type="button" className="ox-link" aria-label={"Forget " + note.topic} onClick={() => setConfirming(note.path)}>Forget</button>
                                            </span>
                                        )}
                                    </li>
                                );
                            })}
                        </ul>
                    </section>
                ))}
            </div>
        );
    }

    return (
        <div className="ox-knows">
            <TeachBox
                label={"Something for " + pod.name + " to remember"}
                placeholder="Tell me something to remember"
                button="Remember"
                hint="I’ll write it down in a chat with you, so you see exactly what I keep."
                onSend={tell}
            />
            {body}
            {notes.isSuccess && topics.length > 0 && (
                <p className="ox-knows-foot">I keep these as files under memory. Shared ones are open to everyone in {pod.name}; the ones marked Only you are kept for you alone.</p>
            )}
            <p className="sr-only" role="status">{said}</p>
        </div>
    );
}

/* ── skills ──────────────────────────────────────────────────────────── */

/** What the datastore says about a skill folder that ships with every
 *  Lemma (`system_skill_files.py`). The only sign of where a skill came
 *  from that is on record; anything else is a skill written in here. */
const BUILT_IN = "Built-in system skill";

const skillKey = (card: SkillCard) => card.folder + "@" + card.updated;
const presentSkills = (cards: readonly SkillCard[] | undefined): Present[] | undefined =>
    cards?.map((card) => ({ key: skillKey(card), at: Date.parse(card.updated) || 0 }));

/** "Prepare a meeting brief", from whatever somebody typed, for the card
 *  that stands in while the Lemma writes the skill up. */
function workingTitle(text: string): string {
    const words = text.replace(/\s+/g, " ").trim().split(" ");
    const title = words.slice(0, 6).join(" ");
    return title.charAt(0).toUpperCase() + title.slice(1) + (words.length > 6 ? "…" : "");
}

/** A Lemma's Skills page: how it does things, written down, one card each.
 *
 *  Its own cards rather than the About page's deck, which turns over and
 *  travels in a track; here every skill can be read at once, so
 *  each card says what the skill does and opens its SKILL.md. Skills that
 *  ship with every Lemma are kept apart, after the ones written here, so
 *  what this Lemma was taught is what you see first. Teaching a new one is
 *  a chat, because the Lemma has to ask how you do it before it can write it. */
export function SkillsPage({ pod, go, actions }: PageProps) {
    const cache = useQueryClient();
    const asked = useAsked("skills:" + pod.id);
    const skills = useQuery({
        queryKey: skillsKey(pod.id),
        queryFn: () => readSkills(pod.id, cache),
        staleTime: SKILLS_FRESH,
        refetchInterval: (query) => (settle(asked.list, presentSkills(query.state.data)).waiting.length > 0 ? 15_000 : false),
    });
    /* Which folders shipped with the platform: the same listing the reader
       makes, read once more for the one field the cards do not carry. */
    const origins = useQuery({
        queryKey: ["skills-built-in", pod.id],
        queryFn: async () => {
            const builtIn = new Set<string>();
            let next: string | undefined;
            for (let page = 0; page < 4; page += 1) {
                const listed = await source.listLibrary(pod.id, "files", "/skills", next);
                for (const item of listed.items) {
                    if (item.kind === "folder" && item.description === BUILT_IN) builtIn.add(item.path.replace(/\/+$/, ""));
                }
                next = listed.next || undefined;
                if (!next) break;
            }
            return builtIn;
        },
        staleTime: SKILLS_FRESH,
        retry: false,
    });

    const cards = useMemo(() => skills.data ?? [], [skills.data]);
    const { waiting, landed } = settle(asked.list, presentSkills(skills.data));
    const [own, shipped] = useMemo(() => {
        const builtIn = origins.data ?? new Set<string>();
        const time = (card: SkillCard) => Date.parse(card.updated) || 0;
        return [
            cards.filter((card) => !builtIn.has(card.dir)).sort((a, b) => time(b) - time(a)),
            cards.filter((card) => builtIn.has(card.dir)).sort((a, b) => a.title.localeCompare(b.title)),
        ];
    }, [cards, origins.data]);
    const unloadable = sayUnloadable(cards);

    function teach(text: string) {
        asked.add(text, skills.data ? skills.data.map(skillKey) : null);
        actions.ask(pod.id, "Learn a new skill: " + text);
    }

    const open = (card: SkillCard) => go({ name: "file", podId: pod.id, path: card.file });
    const now = Date.now();
    let body: ReactNode;
    if (skills.isPending || origins.isPending) {
        body = <SkeletonRows label={"Reading " + pod.name + "’s skills"} rows={4} slow={"Still reading " + pod.name + "’s skills. It’s slower than usual."} />;
    } else if (skills.isError && isForbidden(skills.error)) {
        body = <Calm icon="lock" title={"You can’t read " + pod.name + "’s skills"}>Whoever looks after {pod.name} can.</Calm>;
    } else if (skills.isError) {
        body = (
            <Calm tone="warn" title={"Couldn’t read " + pod.name + "’s skills"} note={codeOf(skills.error)} actions={<TryAgain busy={skills.isFetching} onClick={() => void skills.refetch()} />}>
                Nothing in them changed. You can still teach {pod.name} something above.
            </Calm>
        );
    } else {
        body = (
            <>
                {own.length === 0 && waiting.length === 0 ? (
                    <p className="ox-knows-quiet">Nobody has taught me anything here yet. Tell me how you do something, in your own words.</p>
                ) : (
                    <ul className="ox-knows-skills" aria-label={"Skills written for " + pod.name}>
                        {waiting.map((one) => (
                            <li key={"asked" + one.id}>
                                <div className="ox-knows-skill" data-writing="">
                                    <span className="ox-knows-skill__top">
                                        <span className="ox-knows-skill__ico"><Icon name="skill" size={16} /></span>
                                        <button type="button" className="ox-link" onClick={() => asked.dismiss(one.id)}>Dismiss</button>
                                    </span>
                                    <span className="ox-knows-skill__t">{workingTitle(one.text)}</span>
                                    <span className="ox-knows-skill__when">
                                        {source.label === "live" && now - one.at < WRITING_FOR && <i className="ox-knows-spin" aria-hidden="true" />}
                                        {askedLine(one, "Writing it up in our chat…", now)}
                                    </span>
                                </div>
                            </li>
                        ))}
                        {own.map((card) => (
                            <li key={card.folder}>
                                <SkillButton card={card} fresh={landed.has(skillKey(card))} onOpen={() => open(card)} />
                            </li>
                        ))}
                    </ul>
                )}
                {shipped.length > 0 && (
                    <section className="ox-knows-group" aria-label="Built in">
                        <header className="ox-knows-group__head">
                            <h2>Built in</h2>
                            <span className="ox-knows-num">{shipped.length}</span>
                            <span className="ox-knows-group__note">Every Lemma comes with these.</span>
                        </header>
                        <ul className="ox-knows-skills">
                            {shipped.map((card) => (
                                <li key={card.folder}>
                                    <SkillButton card={card} fresh={false} onOpen={() => open(card)} />
                                </li>
                            ))}
                        </ul>
                    </section>
                )}
                {unloadable && <p className="ox-knows-foot">{unloadable}</p>}
            </>
        );
    }

    return (
        <div className="ox-knows">
            <TeachBox
                label={"Something for " + pod.name + " to learn"}
                placeholder="Teach me how you do something"
                button="Teach me"
                hint="I’ll ask what I need in a chat, then write it up so I do it the same way every time."
                onSend={teach}
            />
            {body}
        </div>
    );
}

/** One skill: what it is called, what it does, and when its file last
 *  changed -- or, for one that cannot be loaded, why, since a skill that
 *  quietly does nothing is worse than one that says so. Opens its SKILL.md. */
function SkillButton({ card, fresh, onOpen }: { card: SkillCard; fresh: boolean; onOpen: () => void }) {
    const does = splitDescription(card.description).does;
    const changed = revisedOn(card.updated);
    return (
        <button type="button" className="ox-knows-skill" data-fresh={fresh || undefined} onClick={onOpen}>
            <span className="ox-knows-skill__top">
                <span className="ox-knows-skill__ico"><Icon name="skill" size={16} /></span>
            </span>
            <span className="ox-knows-skill__t">{card.title}</span>
            {does ? <span className="ox-knows-skill__d">{does}</span> : card.read && <span className="ox-knows-skill__d ox-knows-skill__d--none">No description yet.</span>}
            {card.problem ? (
                <span className="ox-knows-skill__bad">{card.problem}</span>
            ) : (
                changed && <span className="ox-knows-skill__when">Changed {changed}</span>
            )}
        </button>
    );
}

/* ── files ───────────────────────────────────────────────────────────── */

/* The Files page is its own module (`files-page.tsx`): one list, a sheet
   for a file, and the drop. It is exported from here beside Memory and
   Skills, the other two things a Lemma keeps. */
export { FilesPage } from "./files-page";
