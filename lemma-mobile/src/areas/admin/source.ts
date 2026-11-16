import type { AccessSource } from "./access/source";
import type { AboutSource } from "./about/source";
import type { AccountSource } from "./account/source";
import type { BillingSource } from "./billing/source";
import type { ConnectorsSource } from "./connectors/source";
import type { McpSource } from "./mcp/source";
import type { MemorySource } from "./memory/source";
import type { ModelsSource } from "./models/source";
import type { OrgSource } from "./org/source";
import type { PeopleSource } from "./people/source";
import type { ReachSource } from "./reach/source";
import type { SkillsSource } from "./skills/source";
import type { WorksWithSource } from "./works-with/source";

/** Settings: a Lemma's, the account's and the organization's (PARITY.md, F).
 *
 *  Everything these screens read or change, part by part, each part in its
 *  own folder with a live side (`live.ts`) and a sample side (`sample.ts`).
 *  Screens reach it only through `useArea("admin")` (each part's `hooks.ts`),
 *  never the SDK directly, so sample data and a live account are the same
 *  screens. A part per key, so no two parts' calls can collide. */
export interface AdminSource {
    /** Who may do what: a Lemma's actions, the organization's members, the deployment. */
    access: AccessSource;
    /** A Lemma's name, description and face; deleting it. */
    about: AboutSource;
    people: PeopleSource;
    worksWith: WorksWithSource;
    skills: SkillsSource;
    memory: MemorySource;
    mcp: McpSource;
    reach: ReachSource;
    account: AccountSource;
    billing: BillingSource;
    org: OrgSource;
    models: ModelsSource;
    connectors: ConnectorsSource;
}
