import type { AboutLine } from "../types";
import { count } from "../words";
import { cardFor, skillFoldersFrom, SKILLS_ROOT, type ListedItem, type SkillCard } from "@web/skills/skills";

/** A Lemma's skills, read the only way there is: list `/skills`, then open
 *  every `SKILL.md` for its frontmatter (the web's `skills/read-skills.ts`,
 *  less its query cache). Pure, given the two reads, so `tests/` can hold it. */

/** Four pages of fifty is more skills than any Lemma has, but stopping at the
 *  first would draw a shorter list than it truly has, and say nothing. */
const MAX_PAGES = 4;

export type ListPage = (directory: string, page?: string) => Promise<{ items: readonly ListedItem[]; next?: string | null }>;

/** The text of a file, or null when it is not there. */
export type ReadText = (path: string) => Promise<string | null>;

/** The datastore's answer for a folder that is not there: a 400 with this
 *  sentence, or a 404. A Lemma with no `/skills` has no skills. */
export function isNoSuchFolder(error: unknown): boolean {
    const held = error as { statusCode?: unknown; status?: unknown; message?: unknown } | null;
    if (held?.statusCode === 404 || held?.status === 404) return true;
    return /directory not found/i.test(String(held?.message ?? ""));
}

export async function readSkills(list: ListPage, read: ReadText): Promise<SkillCard[]> {
    const items: ListedItem[] = [];
    let next: string | undefined;
    for (let page = 0; page < MAX_PAGES; page += 1) {
        let listed: Awaited<ReturnType<ListPage>>;
        try {
            listed = await list(SKILLS_ROOT, next);
        } catch (error) {
            if (isNoSuchFolder(error)) return [];
            throw error;
        }
        items.push(...listed.items);
        next = listed.next || undefined;
        if (!next) break;
    }
    /* Together, and each on its own: one unreadable SKILL.md must not take
       the others with it, and still gets a row saying so. */
    return Promise.all(skillFoldersFrom(items).map(async (folder) => {
        try {
            const text = await read(folder.file);
            if (text === null) return cardFor(folder, { failure: "Its SKILL.md could not be read. It may be missing, or you may not have access to it." });
            return cardFor(folder, { text });
        } catch {
            return cardFor(folder, { failure: "Its SKILL.md could not be read. It may be missing, or you may not have access to it." });
        }
    }));
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "20 Sep", or "20 Sep 2025" once it is not this year: when the file
 *  behind a skill last changed. */
export function revisedOn(iso: string, now: Date = new Date()): string {
    if (!iso) return "";
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return "";
    const day = `${at.getDate()} ${MONTHS[at.getMonth()]}`;
    return at.getFullYear() === now.getFullYear() ? day : `${day} ${at.getFullYear()}`;
}

/** 820, then 1.2k. */
export function shortCount(words: number): string {
    if (words < 1000) return String(words);
    return (words / 1000).toFixed(1).replace(/\.0$/, "") + "k";
}

/** "820 words · revised 20 Sep": how much instruction, and when it changed. */
export function skillMeta(card: Pick<SkillCard, "words" | "updated">, now: Date = new Date()): string {
    const revised = revisedOn(card.updated, now);
    return [card.words > 0 ? `${shortCount(card.words)} words` : "", revised ? `revised ${revised}` : ""].filter(Boolean).join(" · ");
}

/** The Settings tab's line: "4 skills", or "Nothing yet". */
export function skillsLine(cards: readonly SkillCard[] | null): AboutLine {
    if (cards === null) return { text: null };
    return { text: cards.length ? count(cards.length, "skill", "skills") : "Nothing yet" };
}

/** What a new chat with the Lemma starts with, to write a new skill: the
 *  web's words, which hand the job to the skill whose whole job it is. */
export const NEW_SKILL_ASK = "Write me a new skill. Use the lemma-skill-creator skill: decide its triggers, "
    + "write the instructions, and publish it under /skills. Ask me what it should do first.";
