import { matchMentionable, type Mentionable } from "@web/channels/mentions";
import type { LineDelivery, Member, SourcePlace } from "@web/channels/model";
import { platformName } from "@web/channels/sources";
import type { PersonRef } from "@web/channels/waiting";

/** What a group chat works out for the way it is drawn (design v2): whose
 *  face is whose and in what colour, who sits in the header, where a message
 *  came from and whether one's own arrived, who the mention strip offers,
 *  and where a thread's drawer settles. Pure, for the tests. */

/* ── faces ─────────────────────────────────────────────────────────── */

/** The id a Lemma's face, and so its colour, is drawn from: the Lemma's own
 *  for its front agent (or an agent nobody named), and one of its own for
 *  any other agent in it. Null for a person, who is drawn round instead. */
export function faceIdOf(person: Pick<PersonRef, "kind" | "id">, lemma: { id: string } | null, frontId: string | null): string | null {
    if (person.kind !== "AGENT" || !lemma) return null;
    const front = !person.id || person.id === frontId;
    return front ? lemma.id : lemma.id + ":" + person.id;
}

export interface RoomFace {
    key: string;
    person: PersonRef;
    lemma: boolean;
}

/** Who the header stacks: the room's Lemmas first -- the Lemma itself among
 *  them, since it is in every chat it has, seated or not -- then its people,
 *  never the reader while anybody else is in it; `more` counts the rest. */
export function roomFaces(members: readonly Pick<Member, "person">[], me: string | null, front: PersonRef | null = null, max = 4): { shown: RoomFace[]; more: number; total: number } {
    const isMe = (person: PersonRef) => Boolean(me) && person.kind === "MEMBER" && person.id === me;
    const seated = members.map((one) => one.person);
    const everyone = front && !seated.some((person) => person.kind === "AGENT" && (person.id === front.id || !person.id)) ? [front, ...seated] : seated;
    const faces = everyone.map((person, index): RoomFace => ({
        key: person.kind + ":" + (person.id ?? person.name ?? index),
        person,
        lemma: person.kind === "AGENT",
    }));
    const others = faces.filter((face) => !isMe(face.person));
    const listed = [...(others.length ? others : faces)].sort((a, b) => Number(b.lemma) - Number(a.lemma));
    const shown = listed.slice(0, max);
    return { shown, more: listed.length - shown.length, total: everyone.length };
}

/* ── where it came from, whether it arrived ────────────────────────── */

/** The platforms a name is marked with when somebody writes in from there. */
const MARKED = new Set(["WHATSAPP", "TELEGRAM", "SLACK"]);

/** The platform a message was said on, when it is one a glyph marks:
 *  WhatsApp, Telegram, Slack. Null for a message written in the app. */
export function saidOn(sourceId: string | null, places: readonly Pick<SourcePlace, "id" | "platform">[]): string | null {
    if (!sourceId) return null;
    const platform = places.find((place) => place.id === sourceId)?.platform.toUpperCase() ?? null;
    return platform && MARKED.has(platform) ? platform : null;
}

export interface NotArrived {
    id: string;
    /** "Not delivered to WhatsApp". */
    said: string;
    /** It can be tried again now. */
    retry: boolean;
    /** Why, when the server said, for a screen reader and a second look. */
    why: string | null;
}

export interface Arrival {
    /** Sends that did not arrive, each said in amber under the message. */
    failed: NotArrived[];
    /** "Sending to WhatsApp…", while any is on its way. */
    pending: string | null;
    /** Sends left out on purpose, and why: quiet, not amber. */
    skipped: string[];
}

function whereTo(one: Pick<LineDelivery, "sourceId" | "platform">, places: readonly Pick<SourcePlace, "id" | "platform">[]): string {
    const platform = places.find((place) => place.id === one.sourceId)?.platform ?? one.platform;
    return platform ? platformName(platform) : "outside";
}

/** Whether the reader's own message reached the places outside: a send that
 *  failed says so plainly, by platform, with Try again; one on its way is
 *  said quietly; one that arrived says nothing. */
export function arrivalOf(delivered: readonly LineDelivery[] | null, places: readonly Pick<SourcePlace, "id" | "platform">[], sayWhy: (reason: string | null) => string | null): Arrival {
    const failed: NotArrived[] = [];
    const pending = new Set<string>();
    const skipped: string[] = [];
    for (const one of delivered ?? []) {
        const where = whereTo(one, places);
        if (one.status === "FAILED" || one.status === "DEAD") failed.push({ id: one.id, said: "Not delivered to " + where, retry: true, why: sayWhy(one.reason) });
        else if (one.status === "PENDING") pending.add(where);
        else if (one.status === "SKIPPED") {
            const why = sayWhy(one.reason);
            skipped.push("Not sent to " + where + (why ? ": " + why : ""));
        }
    }
    return { failed, pending: pending.size ? "Sending to " + [...pending].join(" and ") + "…" : null, skipped };
}

/* ── the mention strip ─────────────────────────────────────────────── */

/** Who the strip of faces offers for what follows the `@`: the room's
 *  Lemmas, its people, then Lemmas from outside it (they answer once). */
export function stripOf(people: readonly Mentionable[], query: string, limit = 12): Mentionable[] {
    const fits = matchMentionable(people, query, people.length);
    const rank = (who: Mentionable) => (who.kind === "AGENT" ? (who.inChannel === false ? 2 : 0) : 1);
    return [...fits].sort((a, b) => rank(a) - rank(b)).slice(0, limit);
}

/** The box's words while a Lemma is asked. */
export function askingSaid(name: string): string {
    return "ask " + name + "…";
}

function escaped(name: string): string {
    return name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** What is sent when a Lemma was picked from the strip: the words, led by
 *  its name as the web writes a mention, so it answers and the message
 *  reads the same everywhere. Already named in the words, they stay. */
export function askingText(text: string, name: string): string {
    const said = text.trim();
    if (new RegExp("(^|\\s)@" + escaped(name) + "(?![\\p{L}\\p{N}_])", "u").test(said)) return said;
    return ("@" + name + " " + said).trim();
}

/** The words with the `@query` that opened the strip taken out, once a
 *  Lemma is picked from it: the Lemma is asked by the outline, not a word. */
export function withoutQuery(text: string, at: { start: number }, caret: number): { text: string; caret: number } {
    const before = text.slice(0, at.start).replace(/ $/, "");
    const after = text.slice(caret).replace(/^ /, "");
    const joined = before && after ? before + " " + after : before + after;
    return { text: joined, caret: before.length + (before && after ? 1 : 0) };
}

/* ── a thread's drawer ─────────────────────────────────────────────── */

export type Snap = "full" | "half" | "closed";

/** Where a drawer settles when it is let go, by how far down it was
 *  dragged (`offset`: 0 is full height, `half` is half, `closed` is gone)
 *  and how fast: a flick goes one step the way it was thrown, a slow drag
 *  to the nearer snap, and down past a quarter of what is left closes. */
export function snapAfter(offset: number, velocity: number, half: number, closed: number): Snap {
    "worklet";
    const FLICK = 900;
    if (velocity < -FLICK) return "full";
    if (velocity > FLICK) return offset < half * 0.85 ? "half" : "closed";
    if (offset > half + (closed - half) * 0.28) return "closed";
    return offset < half / 2 ? "full" : "half";
}

/** How far down each snap sits, for a drawer `full` tall that opens to `half`. */
export function snapOffset(snap: Snap, full: number, half: number): number {
    "worklet";
    return snap === "full" ? 0 : snap === "half" ? Math.max(0, full - half) : full;
}
