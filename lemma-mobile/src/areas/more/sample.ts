import { beginSample } from "./begin/sample";
import { callSample } from "./call/sample";
import { computerSample } from "./computer/sample";
import { installSample } from "./install/sample";
import { searchSample } from "./search/sample";
import { shareSample } from "./share/sample";
import type { MoreSource } from "./source";
import { tourSample } from "./tour/sample";
import { welcomeSample } from "./welcome/sample";

/** Search, starting out, sharing, the agent's computer, calls and the tour,
 *  as sample data: shaped like a real organization (Acme, with Ada in it),
 *  never a real one. Writes stay in memory while the app is open, so every
 *  screen can be tried. */
export function moreSample(): MoreSource {
    return {
        search: searchSample(),
        welcome: welcomeSample(),
        begin: beginSample(),
        install: installSample(),
        share: shareSample(),
        computer: computerSample(),
        call: callSample(),
        tour: tourSample(),
    };
}
