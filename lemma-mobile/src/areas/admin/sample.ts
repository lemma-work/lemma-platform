import { accessSample } from "./access/sample";
import { aboutSample } from "./about/sample";
import { accountSample } from "./account/sample";
import { billingSample } from "./billing/sample";
import { connectorsSample } from "./connectors/sample";
import { mcpSample } from "./mcp/sample";
import { memorySample } from "./memory/sample";
import { modelsSample } from "./models/sample";
import { orgSample } from "./org/sample";
import { peopleSample } from "./people/sample";
import { reachSample } from "./reach/sample";
import { skillsSample } from "./skills/sample";
import { worksWithSample } from "./works-with/sample";
import type { AdminSource } from "./source";

/** Settings as sample data: shaped like a real organization (Acme, with
 *  Ada as its owner), never a real one. Writes stay in memory while the app
 *  is open, so every screen can be tried. */
export function adminSample(): AdminSource {
    return {
        access: accessSample(),
        about: aboutSample(),
        people: peopleSample(),
        worksWith: worksWithSample(),
        skills: skillsSample(),
        memory: memorySample(),
        mcp: mcpSample(),
        reach: reachSample(),
        account: accountSample(),
        billing: billingSample(),
        org: orgSample(),
        models: modelsSample(),
        connectors: connectorsSample(),
    };
}
