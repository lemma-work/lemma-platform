import type { ComponentType } from "react";
import { useLemma } from "../../data/hooks";
import { EditAboutScreen } from "./about/screens";
import { ProfileScreen } from "./account/screens";
import { OrgUsageScreen, PlanScreen, TeamBillingScreen, UsageScreen } from "./billing/screens";
import { ConnectorsScreen } from "./connectors/screens";
import { McpScreen } from "./mcp/screens";
import { MemoryScreen } from "./memory/screens";
import { LemmaModelScreen, ModelsScreen } from "./models/screens";
import { OrgPeopleScreen } from "./org/screens";
import { PeopleScreen } from "./people/screens";
import { ChannelsScreen } from "./reach/screens";
import type { LemmaPartId, SettingsSectionId } from "./sections";
import { SkillsScreen } from "./skills/screens";
import type { PartProps } from "./types";
import { WorksWithScreen } from "./works-with/screens";

/** Which screen draws each setting: the account's and the organization's
 *  (`(in)/settings/[section]`), and a Lemma's (`(in)/lemma/[podId]/settings/[section]`).
 *  Each is its part's own component, from its own folder. */
export const SETTINGS_SCREENS: Record<SettingsSectionId, ComponentType> = {
    profile: ProfileScreen,
    usage: UsageScreen,
    plan: PlanScreen,
    people: OrgPeopleScreen,
    connectors: ConnectorsScreen,
    models: ModelsScreen,
    "org-usage": OrgUsageScreen,
    "team-billing": TeamBillingScreen,
};

/** The organization's connectors, reached from a Lemma's Settings too,
 *  with the way back to it. */
function LemmaConnectors({ podId }: PartProps) {
    return <ConnectorsScreen back={useLemma(podId)?.name ?? "Settings"} />;
}

export const LEMMA_SCREENS: Record<LemmaPartId, ComponentType<PartProps>> = {
    about: EditAboutScreen,
    model: LemmaModelScreen,
    skills: SkillsScreen,
    memory: MemoryScreen,
    connectors: LemmaConnectors,
    channels: ChannelsScreen,
    mcp: McpScreen,
    people: PeopleScreen,
    "works-with": WorksWithScreen,
};
