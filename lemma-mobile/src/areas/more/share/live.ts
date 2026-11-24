import type { LiveContext } from "../../context";
import type { Reach, ShareSource } from "./source";

const said = (value: unknown): string | null => (typeof value === "string" && value.trim() ? value.trim() : null);

/** The calls behind what the web reads off its lists: a library row's
 *  `visibility` and `enable_rls` (`files.list`, `tables.list`), an app
 *  tab's `visibility` (`apps.list`), asked of the one thing instead. */
export function shareLive(context: LiveContext): ShareSource {
    const pod = (podId: string) => context.client(podId);
    return {
        async reach(podId, thing): Promise<Reach> {
            switch (thing.kind) {
                case "file": {
                    const detail = await pod(podId).files.get(thing.path);
                    return { visibility: said(detail.visibility), rowsOwn: false };
                }
                case "table": {
                    const detail = await pod(podId).tables.get(thing.name);
                    return { visibility: said(detail.visibility), rowsOwn: detail.enable_rls === true };
                }
                case "app": {
                    const detail = await pod(podId).apps.get(thing.name);
                    return { visibility: said(detail.visibility), rowsOwn: false };
                }
            }
        },
    };
}
