import { readText } from "../../../data/io";
import type { LiveContext } from "../../context";
import type { SkillsSource } from "./source";

/** The web's reads (`listLibrary` over `/skills`, then `readFile` for each
 *  `SKILL.md`). */
export function skillsLive(context: LiveContext): SkillsSource {
    return {
        async list(podId, directory, page) {
            const listed = await context.client(podId).files.list({ directoryPath: directory, limit: 50, pageToken: page });
            return {
                items: listed.items.map((one) => ({
                    name: one.name,
                    kind: /folder|directory/i.test(String(one.kind)) ? "folder" : "file",
                    path: one.path,
                    updated: one.updated_at,
                })),
                next: listed.next_page_token ?? null,
            };
        },
        async read(podId, path) {
            return readText(context.server, podId, path);
        },
    };
}
