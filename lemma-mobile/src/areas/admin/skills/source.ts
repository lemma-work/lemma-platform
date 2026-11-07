import type { ListedItem } from "@web/skills/skills";

/** A Lemma's skills: what people wrote for it to follow (PARITY.md, F).
 *
 *  There is no skills endpoint: a skill is a folder under `/skills` with a
 *  `SKILL.md` in it, so the web lists the folder and opens each file
 *  (`skills/read-skills.ts`). These are those two reads; `model.ts` makes
 *  skills of them. The live side is `live.ts`, the sample side `sample.ts`;
 *  screens reach it through `hooks.ts`. */
export interface SkillsSource {
    /** One page of a folder's listing. */
    list(podId: string, directory: string, page?: string): Promise<{ items: ListedItem[]; next: string | null }>;
    /** A file's text, or null when it is not there. */
    read(podId: string, path: string): Promise<string | null>;
}
