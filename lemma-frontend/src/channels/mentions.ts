/** Naming somebody in a line: the `@` picker, and the mentions a post sends.
 *
 *  What is typed stays plain text -- "@Lem can you…" -- and the people picked
 *  from the menu are remembered beside it. A post names each one whose "@Name"
 *  is still in the text when it is sent, so deleting the words un-names them,
 *  and typing "@Lem" by hand without picking names nobody: two people may
 *  share a name, and only the menu knows which one was meant. */

export interface Mentionable {
    kind: "MEMBER" | "CONTACT" | "AGENT";
    id: string;
    name: string;
    /** In the channel already. An agent from outside it answers once. */
    inChannel?: boolean;
}

export interface Mention {
    kind: "MEMBER" | "CONTACT" | "AGENT";
    id: string;
}

/** The `@` being typed at the caret, if there is one: where it starts and
 *  what follows it. An `@` counts at the start or after a space, so an email
 *  address never opens the menu. */
export function mentionAt(text: string, caret: number): { start: number; query: string } | null {
    const before = text.slice(0, caret);
    const match = /(^|\s)@([^\s@]{0,40})$/.exec(before);
    if (!match) return null;
    return { start: before.length - match[2].length - 1, query: match[2] };
}

/** Who the menu offers for what was typed: anyone a word of whose name starts
 *  with it, in the order given -- `mentionables` puts the channel's agents
 *  first. */
export function matchMentionable(people: readonly Mentionable[], query: string, limit = 6): Mentionable[] {
    const wanted = query.toLowerCase();
    const fits = (who: Mentionable) => !wanted
        || who.name.toLowerCase().startsWith(wanted)
        || who.name.toLowerCase().split(/\s+/).some((word) => word.startsWith(wanted));
    return people.filter(fits).slice(0, limit);
}

/** The text with the `@query` at the caret replaced by the person picked,
 *  and where the caret goes after them. */
export function insertMention(text: string, at: { start: number }, caret: number, who: Mentionable): { text: string; caret: number } {
    const name = "@" + who.name + " ";
    const next = text.slice(0, at.start) + name + text.slice(caret).replace(/^ /, "");
    return { text: next, caret: at.start + name.length };
}

function escaped(name: string): string {
    return name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** The mentions a post sends: each person picked whose "@Name" is still in
 *  the text, once. */
export function mentionsIn(text: string, picked: readonly Mentionable[]): Mention[] {
    const out: Mention[] = [];
    const seen = new Set<string>();
    for (const who of picked) {
        const key = who.kind + ":" + who.id;
        if (seen.has(key)) continue;
        if (!new RegExp("(^|\\s)@" + escaped(who.name) + "(?![\\p{L}\\p{N}_])", "u").test(text)) continue;
        seen.add(key);
        out.push({ kind: who.kind, id: who.id });
    }
    return out;
}

/** Everyone who can be named in a channel, in the order the `@` menu offers
 *  them: the channel's own agents, then the people in it other than the
 *  reader, then the pod's other members who can read it (`readers`), then
 *  the pod's other agents -- naming one answers once. Each once; nobody
 *  without an id or a name. */
export function mentionables(
    agents: readonly { id: string; label: string }[],
    people: readonly { kind: "MEMBER" | "CONTACT" | "AGENT"; id: string | null; name: string | null }[],
    me: string | null,
    readers: readonly { userId: string; name: string }[] = [],
): Mentionable[] {
    const out: Mentionable[] = [];
    const seen = new Set<string>();
    const add = (who: Mentionable) => {
        const key = who.kind + ":" + who.id;
        if (seen.has(key)) return;
        seen.add(key);
        out.push(who);
    };
    for (const person of people) {
        if (person.kind !== "AGENT" || !person.id || !person.name) continue;
        add({ kind: "AGENT", id: person.id, name: person.name, inChannel: true });
    }
    for (const person of people) {
        if (person.kind === "AGENT" || !person.id || !person.name) continue;
        if (person.kind === "MEMBER" && person.id === me) continue;
        add({ kind: person.kind, id: person.id, name: person.name, inChannel: true });
    }
    for (const reader of readers) {
        if (!reader.userId || !reader.name || reader.userId === me) continue;
        add({ kind: "MEMBER", id: reader.userId, name: reader.name, inChannel: false });
    }
    for (const agent of agents) if (agent.id && agent.label) add({ kind: "AGENT", id: agent.id, name: agent.label, inChannel: false });
    return out;
}

/** The pod's members who can read a channel without being in it: everyone,
 *  for a public group; nobody else's own channel, a private group or a
 *  contact's is theirs to read, so naming them would name somebody who
 *  cannot see the line. Teammates (agents) are named as agents instead. */
export function channelReaders<M extends { userId?: string; name: string; kind?: string }>(
    channel: { kind: "DIRECT" | "CONTACT" | "GROUP"; visibility: "PUBLIC" | "PRIVATE" },
    members: readonly M[],
): { userId: string; name: string }[] {
    if (channel.kind !== "GROUP" || channel.visibility !== "PUBLIC") return [];
    return members.flatMap((member) => member.userId && member.kind !== "teammate" ? [{ userId: member.userId, name: member.name }] : []);
}

/** The text with one picked name taken out -- its chip removed -- and the
 *  space after it, so "ask @Lem now" becomes "ask now". */
export function removeMention(text: string, who: Pick<Mentionable, "name">): string {
    return text.replace(new RegExp("(^|\\s)@" + escaped(who.name) + "(?![\\p{L}\\p{N}_]) ?", "u"), "$1");
}

/** The people picked whose names are still in the text: the chips. */
export function pickedIn(text: string, picked: readonly Mentionable[]): Mentionable[] {
    const named = new Set(mentionsIn(text, picked).map((one) => one.kind + ":" + one.id));
    const seen = new Set<string>();
    return picked.filter((who) => {
        const key = who.kind + ":" + who.id;
        if (!named.has(key) || seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}
