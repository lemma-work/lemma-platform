import { tablesSample } from "../../tables/sample";
import type { Reach, ShareSource } from "./source";

/** Who can open each sample thing. A table's reach is the tables sample's
 *  own (`tables/sample.ts`: the books are restricted), so the Tables tab and
 *  the sheet never disagree. Files and apps cover every case between them:
 *  a private draft, a page anyone signed in can open, a contract only some
 *  people may, an app open to anyone with its address. The people are the
 *  settings sample's (`admin/people/sample.ts`): Support has three, Books
 *  only Ada. */

const FILES: Record<string, string> = {
    "/Notes/Prices.html": "PUBLIC",
    "/Notes/Contract.docx": "RESTRICTED",
};

const APPS: Record<string, string> = {
    "refund-desk": "PUBLIC",
    "press-list": "PERSONAL",
    "renewal-radar": "RESTRICTED",
};

const settle = <T,>(value: T) => new Promise<T>((done) => setTimeout(() => done(value), 160));

export function shareSample(): ShareSource {
    const tables = tablesSample();
    return {
        async reach(podId, thing): Promise<Reach> {
            switch (thing.kind) {
                case "file":
                    return settle({ visibility: thing.path.startsWith("/me/") ? "PERSONAL" : FILES[thing.path] ?? "POD", rowsOwn: false });
                case "table": {
                    const shape = await tables.shape(podId, thing.name);
                    return settle({ visibility: shape.visibility ?? null, rowsOwn: shape.enable_rls === true });
                }
                case "app":
                    return settle({ visibility: APPS[thing.name] ?? "POD", rowsOwn: false });
            }
        },
    };
}
