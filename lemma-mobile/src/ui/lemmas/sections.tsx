import type { AppItem } from "../../data/types";
import { SettingsTab } from "../../areas/admin/tab";
import { ChatsTab, PeopleTab } from "../../areas/chats/tab";
import { FilesTab } from "../../areas/files/tab";
import { TablesTab } from "../../areas/tables/tab";
import { PlanTab, RunsTab } from "../../areas/work/tab";
import { AppTile } from "../app-tile";
import { Empty } from "../chrome";
import { View } from "react-native";

/** Everything a Lemma has, as sections listed on its page. Each opens its
 *  own screen (`(in)/lemma/[podId]/section/[name]`) holding the area's own
 *  component (`areas/<area>/tab.tsx`), so an area changes its section without
 *  touching the page. */

export type SectionName = "chats" | "apps" | "tables" | "files" | "runs" | "people" | "plan" | "settings";

export const SECTIONS: readonly { name: SectionName; label: string }[] = [
    { name: "chats", label: "Chats" },
    { name: "apps", label: "Apps" },
    { name: "tables", label: "Tables" },
    { name: "files", label: "Files" },
    { name: "runs", label: "Runs" },
    { name: "people", label: "People" },
    { name: "plan", label: "Plan" },
    { name: "settings", label: "Settings" },
];

export function isSection(name: string | undefined): name is SectionName {
    return SECTIONS.some((one) => one.name === name);
}

export function SectionBody({ name, podId, lemmaName, apps }: { name: SectionName; podId: string; lemmaName: string; apps: AppItem[] }) {
    switch (name) {
        case "chats": return <ChatsTab podId={podId} name={lemmaName} />;
        case "apps": return <AppGrid apps={apps} />;
        case "tables": return <TablesTab podId={podId} name={lemmaName} />;
        case "files": return <FilesTab podId={podId} name={lemmaName} />;
        case "runs": return <RunsTab podId={podId} name={lemmaName} />;
        case "people": return <PeopleTab podId={podId} name={lemmaName} />;
        case "plan": return <PlanTab podId={podId} name={lemmaName} />;
        case "settings": return <SettingsTab podId={podId} name={lemmaName} />;
    }
}

function AppGrid({ apps }: { apps: AppItem[] }) {
    if (apps.length === 0) return <Empty title="No apps yet." />;
    const pairs: AppItem[][] = [];
    for (let index = 0; index < apps.length; index += 2) pairs.push(apps.slice(index, index + 2));
    return (
        <View style={{ gap: 16, paddingHorizontal: 4 }}>
            {pairs.map((pair, index) => (
                <View key={index} style={{ flexDirection: "row", gap: 12 }}>
                    {pair.map((app) => <AppTile key={app.id} app={app} />)}
                    {pair.length === 1 ? <View style={{ flex: 1 }} /> : null}
                </View>
            ))}
        </View>
    );
}
