import { View } from "react-native";
import { useRouter } from "expo-router";
import { useLemma, useOrg } from "../../data/hooks";
import { Press } from "../../ui/press";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { useOrgMembers } from "./access/hooks";
import { startedLine } from "./about/faces";
import { useOrigin } from "./about/hooks";
import { DeleteLemma } from "./about/screens";
import { useConnectorsLine } from "./connectors/hooks";
import { useMemoryLine } from "./memory/hooks";
import { useMcpLine } from "./mcp/hooks";
import { useModelLine } from "./models/hooks";
import { usePeopleLine } from "./people/hooks";
import { useChannelsLine } from "./reach/hooks";
import { adminRoutes } from "./routes";
import { LEMMA_PARTS, type LemmaPartId } from "./sections";
import { useSkillsLine } from "./skills/hooks";
import type { AboutLine } from "./types";
import { Group, Row } from "./ui";
import { memberName } from "@web/org/membership";
import { useWorksWithLine } from "./works-with/hooks";

/** A Lemma's settings, on its page: what it is for and who started it,
 *  then a row for each part of how it works, each saying what is true there
 *  now (the web's About overview, its tiles as rows). What the organization
 *  shares is one tap away; deleting it comes last. */
export function SettingsTab({ podId, name }: { podId: string; name: string }) {
    const router = useRouter();
    const lemma = useLemma(podId);
    const { org } = useOrg();
    const origin = useOrigin(podId);
    const members = useOrgMembers(org?.id);
    const lines = useLines(podId);
    const by = (members.data ?? []).find((member) => member.user_id && member.user_id === origin.data?.createdBy);
    const started = startedLine(origin.data?.createdAt ?? null, by ? memberName(by) : null);
    const open = (part: LemmaPartId) => router.push(adminRoutes.lemmaSection(podId, part) as never);

    return (
        <View style={{ paddingHorizontal: 4, gap: 22, paddingTop: 4, paddingBottom: 12 }}>
            <View style={{ gap: 6 }}>
                {lemma?.description ? (
                    <Text size={15.5} style={{ lineHeight: 22 }}>{lemma.description}</Text>
                ) : (
                    <Text size={15.5} tone={color.ink3}>Nothing written yet about what it is for.</Text>
                )}
                {started ? <Text size={13} tone={color.ink3}>{started}</Text> : null}
                <Press role="link" label={`Change ${name}'s name, face and what it is for`} onPress={() => router.push(adminRoutes.lemmaSection(podId, "about") as never)} style={{ alignSelf: "flex-start", minHeight: 40, justifyContent: "center" }}>
                    <Text size={15} weight="medium">Edit name and face</Text>
                </Press>
            </View>

            <Group label="How it works">
                {LEMMA_PARTS.map((part, index) => {
                    const line = lines[part.id];
                    return (
                        <Row
                            key={part.id}
                            role="link"
                            label={part.label}
                            hint={line?.text ?? part.blurb}
                            needs={Boolean(line?.needs)}
                            last={index === LEMMA_PARTS.length - 1}
                            onPress={() => open(part.id)}
                        />
                    );
                })}
            </Group>

            {org ? (
                <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", columnGap: 14 }}>
                    <Text size={13.5} tone={color.ink3}>Shared by everyone in {org.name}:</Text>
                    <Press role="link" label="Models" onPress={() => router.push(adminRoutes.section("models") as never)} style={{ minHeight: 40, justifyContent: "center" }}>
                        <Text size={14} weight="medium">Models</Text>
                    </Press>
                    <Press role="link" label="Usage" onPress={() => router.push(adminRoutes.section("usage") as never)} style={{ minHeight: 40, justifyContent: "center" }}>
                        <Text size={14} weight="medium">Usage</Text>
                    </Press>
                </View>
            ) : null}

            <DeleteLemma podId={podId} name={name} />
        </View>
    );
}

/** Every part's line at once, each read through the query its own screen
 *  uses, so opening a part after the tab draws from what was fetched. */
function useLines(podId: string): Partial<Record<LemmaPartId, AboutLine>> {
    return {
        model: useModelLine(podId),
        skills: useSkillsLine(podId),
        memory: useMemoryLine(podId),
        connectors: useConnectorsLine(podId),
        channels: useChannelsLine(podId),
        mcp: useMcpLine(podId),
        people: usePeopleLine(podId),
        "works-with": useWorksWithLine(podId),
    };
}
