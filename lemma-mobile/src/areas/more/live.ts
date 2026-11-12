import type { LiveContext } from "../context";
import { beginLive } from "./begin/live";
import { callLive } from "./call/live";
import { computerLive } from "./computer/live";
import { installLive } from "./install/live";
import { searchLive } from "./search/live";
import { shareLive } from "./share/live";
import type { MoreSource } from "./source";
import { tourLive } from "./tour/live";
import { welcomeLive } from "./welcome/live";

/** Search, starting out, sharing, the agent's computer, calls and the tour,
 *  from the server. */
export function moreLive(context: LiveContext): MoreSource {
    return {
        search: searchLive(context),
        welcome: welcomeLive(context),
        begin: beginLive(context),
        install: installLive(context),
        share: shareLive(context),
        computer: computerLive(context),
        call: callLive(context),
        tour: tourLive(context),
    };
}
