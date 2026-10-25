import type { LiveContext } from "../../context";
import type { MemorySource } from "./source";

/** The web's read (`listLibrary` over each note folder). */
export function memoryLive(context: LiveContext): MemorySource {
    return {
        async list(podId, directory, page) {
            const listed = await context.client(podId).files.list({ directoryPath: directory, limit: 50, pageToken: page });
            return {
                items: listed.items.map((one) => ({
                    id: one.id,
                    name: one.name,
                    kind: /folder|directory/i.test(String(one.kind)) ? "folder" as const : "file" as const,
                    path: one.path,
                    updated: one.updated_at,
                    detail: one.description || one.mime_type || String(one.kind),
                    ...(one.description ? { description: one.description } : {}),
                })),
                next: listed.next_page_token ?? null,
            };
        },
    };
}
