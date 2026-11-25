import type { LiveContext } from "../context";
import { accessLive } from "./access/live";
import { aboutLive } from "./about/live";
import { accountLive } from "./account/live";
import { billingLive } from "./billing/live";
import { connectorsLive } from "./connectors/live";
import { mcpLive } from "./mcp/live";
import { memoryLive } from "./memory/live";
import { modelsLive } from "./models/live";
import { orgLive } from "./org/live";
import { peopleLive } from "./people/live";
import { reachLive } from "./reach/live";
import { skillsLive } from "./skills/live";
import { worksWithLive } from "./works-with/live";
import type { AdminSource } from "./source";

/** Settings: a Lemma's, the account's and the organization's, from the server. */
export function adminLive(context: LiveContext): AdminSource {
    return {
        access: accessLive(context),
        about: aboutLive(context),
        people: peopleLive(context),
        worksWith: worksWithLive(context),
        skills: skillsLive(context),
        memory: memoryLive(context),
        mcp: mcpLive(context),
        reach: reachLive(context),
        account: accountLive(context),
        billing: billingLive(context),
        org: orgLive(context),
        models: modelsLive(context),
        connectors: connectorsLive(context),
    };
}
