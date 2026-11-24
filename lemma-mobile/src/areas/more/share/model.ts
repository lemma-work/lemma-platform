import { workspace } from "../../../data/routes";
import { part } from "../../paths";
import type { Reach, SharedThing, ShareSubject } from "./source";

/** Sharing a Lemma or one thing in it: what the sheet says and which
 *  address it hands out, apart from drawing it. Pure, so `tests/` can hold
 *  it. The web's `space/share-sheet.tsx`, said the phone's way.
 *
 *  Access in Lemma is the Lemma's: anyone in it can open what is in it, so
 *  who has access is its people for every subject, and adding a person adds
 *  them to the Lemma. A thing can reach further or less far than that, and
 *  says so in one line; a file can also go past the Lemma as a bounded
 *  public link, which only a file can do. */

/** What the sheet is called: "Share Support", "Share Weekly update". */
export function shareTitle(subject: ShareSubject, lemmaName: string): string {
    return `Share ${subject.kind === "lemma" ? lemmaName : subject.label}`;
}

const visibilityOf = (reach: Reach | null | undefined) => (reach?.visibility ?? "").toUpperCase();

/** Whether its own address opens it for anyone, signed in or not: an app
 *  published to everyone. A file or a table marked PUBLIC still asks for a
 *  Lemma account, which is what a file's public link is for. */
export function opensForAnyone(thing: ShareSubject, reach: Reach | null | undefined): boolean {
    return thing.kind === "app" && visibilityOf(reach) === "PUBLIC";
}

/** Who can open it, in one line, from what the server says of it (the
 *  web's `whoCanOpen`). */
export function whoCanOpen(thing: SharedThing, reach: Reach, lemmaName: string): string {
    const said = visibilityOf(reach);
    if (thing.kind === "app") {
        /* An app's PUBLIC is the open internet: the server serves it on its
           own address with no session at all. */
        if (said === "PUBLIC") return "Anyone with its address can open this, without signing in.";
        if (said === "PERSONAL") return "Only the person who made it can open this.";
        if (said === "RESTRICTED") return "Only the people it was shared with can open this.";
        return `Everyone in ${lemmaName} can open this.`;
    }
    /* PUBLIC waives the Lemma, not signing in: it is every Lemma account,
       never the open internet. It is also what the Lemma answers people
       outside it from, in the groups where it answers them. */
    if (said === "PUBLIC") return `Anyone with a Lemma account can open this, and people outside ${lemmaName} get answers from it in its groups.`;
    if (said === "PERSONAL") return thing.kind === "file" ? "Only you can open this. It is in your private files." : "Only you can open this.";
    if (said === "RESTRICTED") return "Only the people it was shared with can open this.";
    if (reach.rowsOwn) return `Everyone in ${lemmaName} can open this, and each sees only their own rows.`;
    return `Everyone in ${lemmaName} can open this.`;
}

/** The address handed out for it: the web's own page for a Lemma and a
 *  table, the files area's address for a file (`files.getUrl`'s
 *  `app_url`), and an app's own address where it has one. Null while what
 *  it depends on is still being read. */
export function addressOf(subject: ShareSubject, at: { web: string; podId: string; fileUrl?: string | null; appUrl?: string | null }): string | null {
    const web = at.web.replace(/\/+$/, "");
    switch (subject.kind) {
        case "lemma": return web + workspace.pod(at.podId);
        case "table": return web + workspace.table(at.podId, subject.name);
        case "file": return at.fileUrl ?? null;
        case "app": return at.appUrl ?? web + `/t/${part(at.podId)}/app/${part(subject.name)}`;
    }
}

/** What the link row says: who the address works for. An app open to
 *  anyone, sent at its own address, opens for whoever gets it; everything
 *  else asks somebody outside the Lemma to join first. */
export function linkRow(subject: ShareSubject, reach: Reach | null | undefined, lemmaName: string, ownAddress: boolean): { title: string; line: string; anyone: boolean } {
    if (opensForAnyone(subject, reach)) {
        if (ownAddress) return { title: "Anyone with the address", line: "The app opens without signing in.", anyone: true };
        /* Open to anyone at its own address, which is not this one: this
           link is its page in Lemma, so the line says which link it is. */
        return { title: `People in ${lemmaName}`, line: "This link opens it in Lemma, where anyone else is asked to join first.", anyone: false };
    }
    return { title: `People in ${lemmaName}`, line: "Anyone else is asked to join first.", anyone: false };
}

/** A file's public links, as the row says them. */
export function liveLinksLine(live: number): string {
    if (live <= 0) return "A read-only link that expires.";
    return live === 1 ? "One link is live." : `${live} links are live.`;
}

/** Whether the reader is the only one with access: nobody, or only them. */
export function onlyReader(people: readonly { email: string | null; userId?: string | null }[], me: { email: string | null; id: string | null }): boolean {
    const mail = (me.email ?? "").toLowerCase();
    return people.every((person) => (me.id && person.userId === me.id) || (mail !== "" && (person.email ?? "").toLowerCase() === mail));
}
