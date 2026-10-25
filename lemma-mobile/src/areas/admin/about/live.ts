import { uploadIcon } from "../../../data/io";
import type { LiveContext } from "../../context";
import type { AboutSource } from "./source";

/** The same calls the web makes (`data/live.ts`: `renamePod`, `describePod`,
 *  `setPodIcon`, `uploadIcon`, `deletePod`). */
export function aboutLive(context: LiveContext): AboutSource {
    const pod = (podId: string) => context.client(podId);
    return {
        async origin(podId) {
            const row = (await pod(podId).pods.get(podId)) as { created_at?: string | null; user_id?: string | null };
            return { createdAt: row.created_at ?? null, createdBy: row.user_id ?? null };
        },
        async rename(podId, name) {
            await pod(podId).pods.update(podId, { name });
        },
        async describe(podId, description) {
            await pod(podId).pods.update(podId, { description: description.trim() || null });
        },
        async setIcon(podId, icon) {
            await pod(podId).pods.update(podId, { icon_url: icon });
        },
        async uploadIcon(file) {
            return uploadIcon(context.server, file);
        },
        async remove(podId) {
            await pod(podId).pods.delete(podId);
        },
    };
}
