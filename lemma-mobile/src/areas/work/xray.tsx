import { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { useApps } from "../../data/hooks";
import { routes } from "../../data/routes";
import { copyText } from "../../thread/clipboard";
import { Glyph } from "../../thread/glyphs";
import { Loading } from "../../ui/chrome";
import { Icon } from "../../ui/icons";
import { Button } from "../../ui/press";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { adminRoutes } from "../admin/routes";
import { useFolder } from "../files/hooks";
import { isMissingFolder } from "../files/model";
import { filesRoutes } from "../files/routes";
import { useTableList } from "../tables/hooks";
import { tablesRoutes } from "../tables/routes";
import { readableName } from "@web/library/reading";
import { useAgents, useSchedules, useSurfaces, useWorkflows, useWorkSource } from "./hooks";
import { skillPath, xrayGroups, type Piece } from "./model";
import { Heading, Line, Note } from "./parts";
import { workRoutes } from "./routes";

/** Everything a Lemma runs on, a group each, each piece opening on its own
 *  screen (the `/o` frame's X-ray): what runs on its own, its workflows, its
 *  agents, tables, apps, the skills it follows, where people reach it, and
 *  the address an assistant connects to it by. */
export function Xray({ podId, name }: { podId: string; name: string }) {
    const router = useRouter();
    const source = useWorkSource();
    const schedules = useSchedules(podId, name);
    const workflows = useWorkflows(podId);
    const agents = useAgents(podId, name);
    const tables = useTableList(podId);
    const { apps, pending: appsPending } = useApps();
    const skills = useFolder(podId, "/skills");
    const surfaces = useSurfaces(podId);
    const [copied, setCopied] = useState(false);
    const address = source.mcpUrl(podId);

    const skillsMissing = skills.isError && isMissingFolder(skills.error);
    const groups = xrayGroups({
        lemmaName: name,
        schedules: { data: schedules.data, failed: schedules.isError },
        workflows: { data: workflows.data, failed: workflows.isError },
        agents: { data: agents.data, failed: agents.isError },
        tables: { data: tables.data?.pages.flatMap((page) => page.items).map((table) => ({ name: table.name, title: readableName(table.name), detail: table.columns !== null ? table.columns + (table.columns === 1 ? " column" : " columns") : "" })), failed: tables.isError },
        apps: { data: appsPending ? undefined : apps.filter((app) => app.podId === podId).map((app) => ({ name: app.name, label: app.label, detail: app.description ?? "" })), failed: false },
        skills: { data: skillsMissing ? [] : skills.data?.pages.flatMap((page) => page.items).map((item) => ({ name: item.name, path: skillPath(item), description: item.description ?? "" })), failed: skills.isError && !skillsMissing },
        surfaces: { data: surfaces.data, failed: surfaces.isError },
        href: {
            schedule: (id) => workRoutes.schedule(podId, id),
            workflow: (flow) => workRoutes.workflow(podId, flow),
            agent: (agent) => workRoutes.agent(podId, agent),
            table: (table) => tablesRoutes.table(podId, table),
            app: (app) => routes.app(podId, app),
            file: (path) => filesRoutes.file(podId, path),
            reach: adminRoutes.lemma(podId),
        },
    });

    return (
        <View>
            <Note style={{ paddingTop: 0 }}>Everything {name} runs on. Each piece opens here.</Note>
            {groups.map((group, index) => (
                <View key={group.title}>
                    <Heading first={index === 0} count={group.pieces?.length}>{group.title}</Heading>
                    {group.pieces === null && !group.failed ? <Loading /> : group.failed ? <Note tone={color.ink3}>Couldn’t read this.</Note> : group.pieces!.length === 0 ? <Note tone={color.ink3}>{group.empty}</Note> : (
                        group.pieces!.map((piece) => (
                            <Line
                                key={piece.key}
                                glyph={<PieceGlyph piece={piece} />}
                                title={piece.name}
                                tone={piece.off ? color.ink2 : color.ink}
                                quiet={piece.line}
                                onPress={piece.href ? () => router.push(piece.href as never) : undefined}
                            />
                        ))
                    )}
                </View>
            ))}
            {address ? (
                <View style={{ marginTop: 22, gap: 8, paddingHorizontal: 4 }}>
                    <Text accessibilityRole="header" size={12.5} tone={color.ink3}>Use {name} from Claude or ChatGPT</Text>
                    <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20.5 }}>One address gives an assistant {name}’s tables, files and apps, with the access of whoever connects it.</Text>
                    <Text size={13.5} selectable tone={color.ink} numberOfLines={2}>{address}</Text>
                    <View style={{ flexDirection: "row" }}>
                        <Button title={copied ? "Address copied" : "Copy the address"} kind="outline" onPress={() => { copyText(address); setCopied(true); }} />
                    </View>
                </View>
            ) : null}
        </View>
    );
}

function PieceGlyph({ piece }: { piece: Piece }) {
    const tone = piece.off ? color.ink3 : color.ink2;
    switch (piece.icon) {
        case "agent": return <Glyph name="agent" size={16} tone={tone} />;
        case "skill": return <Icon name="file" size={16} tone={tone} />;
        case "plug": return <Glyph name="plug" size={16} tone={tone} />;
        default: return <Icon name={piece.icon} size={16} tone={tone} />;
    }
}
