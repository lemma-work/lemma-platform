import type { Server } from "../session/server";
import { liveContext } from "./context";
import { adminLive } from "./admin/live";
import { adminSample } from "./admin/sample";
import type { AdminSource } from "./admin/source";
import { chatLive } from "./chat/live";
import { chatSample } from "./chat/sample";
import type { ChatSource } from "./chat/source";
import { chatsLive } from "./chats/live";
import { chatsSample } from "./chats/sample";
import type { ChatsSource } from "./chats/source";
import { filesLive } from "./files/live";
import { filesSample } from "./files/sample";
import type { FilesSource } from "./files/source";
import { moreLive } from "./more/live";
import { moreSample } from "./more/sample";
import type { MoreSource } from "./more/source";
import { tablesLive } from "./tables/live";
import { tablesSample } from "./tables/sample";
import type { TablesSource } from "./tables/source";
import { workLive } from "./work/live";
import { workSample } from "./work/sample";
import type { WorkSource } from "./work/source";

/** The app, area by area (PARITY.md). Each area keeps its reads, its sample
 *  data, its hooks, its routes and its tab on a Lemma's page in its own
 *  folder, so work on one never edits another's files. `data/source.ts` is
 *  the core the first screens were built on: Home, Needs you, the Lemmas. */
export interface Areas {
    chat: ChatSource;
    tables: TablesSource;
    files: FilesSource;
    work: WorkSource;
    chats: ChatsSource;
    admin: AdminSource;
    more: MoreSource;
}

export function liveAreas(server: Server): Areas {
    const context = liveContext(server);
    return {
        chat: chatLive(context),
        tables: tablesLive(context),
        files: filesLive(context),
        work: workLive(context),
        chats: chatsLive(context),
        admin: adminLive(context),
        more: moreLive(context),
    };
}

export function sampleAreas(): Areas {
    return {
        chat: chatSample(),
        tables: tablesSample(),
        files: filesSample(),
        work: workSample(),
        chats: chatsSample(),
        admin: adminSample(),
        more: moreSample(),
    };
}
